import os
import logging
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional
from dotenv import load_dotenv
import asyncio
from contextlib import asynccontextmanager
import stripe
from stripe_agent_toolkit.openai.toolkit import StripeAgentToolkit

from agents import Agent, Runner, gen_trace_id, trace, ItemHelpers, MessageOutputItem
from agents.mcp import MCPServerStdio

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Initialize Stripe
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

# Initialize Stripe Agent Toolkit
stripe_agent_toolkit = StripeAgentToolkit(
    secret_key=os.getenv("STRIPE_SECRET_KEY"),
    configuration={
        "actions": {
            "payment_links": {"create": True},
            "products": {"create": True},
            "prices": {"create": True},
        }
    },
)

# Global server instance
_maps_server = None
_server_lock = asyncio.Lock()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    global _maps_server
    try:
        async with _server_lock:
            if _maps_server is None:
                logger.debug("Initializing Google Maps server")
                _maps_server = MCPServerStdio(
                    name="Google Maps Server",
                    params={
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-google-maps"],
                        "env": {
                            "GOOGLE_MAPS_API_KEY": os.getenv("GOOGLE_MAPS_API_KEY")
                        },
                    },
                )
                await _maps_server.connect()
                logger.debug("Server initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize server: {str(e)}", exc_info=True)
        raise
    
    yield
    
    # Shutdown
    if _maps_server:
        try:
            await _maps_server.disconnect()
            logger.debug("Server disconnected successfully")
        except Exception as e:
            logger.error(f"Error disconnecting server: {str(e)}", exc_info=True)

app = FastAPI(title="Travel Assistant API", lifespan=lifespan)

class ConversationMessage(BaseModel):
    role: str = Field(..., description="Rol del mensaje (user, assistant, etc.).")
    content: str = Field(..., description="Contenido del mensaje.")

class ChatQuery(BaseModel):
    query: str = Field(..., title="Consulta", description="Consulta del usuario.")
    history: List[ConversationMessage] = Field(
        default_factory=list,
        title="Historial de conversación",
        description="Mensajes previos para mantener el contexto."
    )
    conversation_id: Optional[str] = Field(
        default=None,
        title="ID de conversación",
        description="Identificador único para la conversación."
    )

class PaymentRequest(BaseModel):
    amount: float = Field(..., description="Amount to charge in the smallest currency unit (e.g., cents)")
    currency: str = Field(default="mxn", description="Currency code (e.g., mxn, usd)")
    description: str = Field(..., description="Description of the payment")
    payment_method: str = Field(..., description="Payment method ID from Stripe")

class Response(BaseModel):
    response: str
    trace_id: str
    conversation_id: Optional[str] = None

class PaymentResponse(BaseModel):
    success: bool
    payment_id: Optional[str] = None
    error: Optional[str] = None

@app.post("/create-payment-intent", response_model=PaymentResponse)
async def create_payment_intent(request: PaymentRequest):
    try:
        # Create a PaymentIntent with the order amount and currency
        intent = stripe.PaymentIntent.create(
            amount=int(request.amount * 100),  # Convert to cents
            currency=request.currency,
            payment_method=request.payment_method,
            description=request.description,
            confirm=True,
            return_url="https://your-domain.com/payment-success"  # Replace with your success URL
        )
        
        return PaymentResponse(
            success=True,
            payment_id=intent.id
        )
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error: {str(e)}")
        return PaymentResponse(
            success=False,
            error=str(e)
        )
    except Exception as e:
        logger.error(f"Error creating payment intent: {str(e)}")
        return PaymentResponse(
            success=False,
            error=str(e)
        )

# Define specialized agents as independent agents
def create_maps_agent(conversation_context=""):
    """Create a maps specialist agent"""
    return Agent(
        name="Maps Specialist",
        model="gpt-4o-mini",
        instructions=f"""You are a travel destination specialist using Google Maps to provide detailed information.
        
        {conversation_context}
        
        For each place you mention, you MUST include:
        1. The name of the place
        2. The complete address
        3. The rating (if available)
        4. The Google Maps URL in the format: https://www.google.com/maps/place/?q=place_id:[place_id]
        
        Format your responses like this:
        **[Place Name]**
        - Address: [Full Address]
        - Rating: [Rating]/5
        - Maps: https://www.google.com/maps/place/?q=place_id:[place_id]
        
        Important: Always use the place_id from the Google Maps API response to construct the URL.
        
        When asked about travel recommendations, suggest multiple attractions and provide context about
        why they are worth visiting. Always be informative, engaging, and thorough in your responses.
        Your goal is to inspire the user about the destination.
        """,
        mcp_servers=[_maps_server],
    )

def create_payments_agent(conversation_context=""):
    """Create a payments specialist agent"""
    return Agent(
        name="Travel Payments Specialist",
        model="gpt-4o-mini",
        instructions=f"""You are a travel booking and payments specialist.
        
        {conversation_context}
        
        When asked to create travel products or options for a destination, ALWAYS:
        1. Create three Stripe products for the given [destino]:
           - "Vuelo a [destino]" (Flight) - Price: $350-600 USD (be creative with the exact price)
           - "Hotel en [destino]" (Hotel for 3 nights) - Price: $150-400 USD per night (calculate for 3 nights, e.g., $450-1200 USD total for hotel)
           - "Paquete completo a [destino]" (Complete Package: Flight + Hotel + 1 Activity) - Price: Offer a ~10-15% discount compared to individual items.
           
        2. Create payment links for EACH of these products (flight, hotel, package).
        
        3. Return a structured summary of the created products and their respective payment links. Format it clearly.
           Example for [destino] = Paris:
           "package_options": {{ 
             "destination": "Paris",
             "flight": {{ "name": "Vuelo a Paris", "price_usd": 500, "payment_link": "https://stripe-link.com/fly-paris" }},
             "hotel": {{ "name": "Hotel en Paris (3 noches)", "price_usd": 600, "payment_link": "https://stripe-link.com/stay-paris" }},
             "package": {{ "name": "Paquete Completo a Paris", "price_usd": 990, "discount_percent": 10, "payment_link": "https://stripe-link.com/paris-package" }}
           }}
           
           CRITICAL: Provide a unique `payment_link` for flight, hotel, AND package within the structured data.
                   The synthesizer agent will then use these to create HTML buttons.
        
        The products should be realistic based on the destination and should have appealing descriptions within the structured data if possible.
        """,
        tools=stripe_agent_toolkit.get_tools(),
    )

def create_synthesizer_agent(conversation_context=""):
    """Create a synthesizer agent to provide coherent responses"""
    return Agent(
        name="Travel Experience Synthesizer",
        model="gpt-4o-mini",
        instructions=f"""You are a travel experience synthesizer that creates personalized, engaging, and helpful responses.
        
        {conversation_context}
        
        Your primary goal is to combine information from different specialist agents (like maps and payments) into a single, seamless, and intuitive response.
        
        Follow these rules for crafting your response:
        
        1.  **Friendly & Engaging Tone**: Always maintain a friendly, enthusiastic, and helpful tone. Make the user feel excited about their travel possibilities.
        2.  **Prioritize User Query**: Directly address the user's original query first.
        3.  **Integrate Information & Offers Smoothly**:
            *   If you receive both destination information (from maps) and travel package offers (from payments) for the SAME destination:
                *   Start by presenting the destination information in an engaging way.
                *   Then, transition naturally to the travel offers. Example: "San Francisco sounds amazing, right? Speaking of planning a trip, I found some great package options for you!"
            *   If you only receive travel package offers, present them clearly and attractively.
            *   If you only receive destination information, present it thoroughly.
        4.  **Formatting for Readability**:
            *   Use Markdown for good structure: headings, bold text, bullet points, and emojis  gương.
            *   When presenting travel packages from the payments specialist, use the structured information they provide. Ensure prices, descriptions, and especially the payment link (as a button) are clear and prominent.
                Example structure for presenting packages:
                "¡Aquí tienes unas opciones fantásticas para tu viaje a [destino]! 🏖️\n\n                ✨ **Paquete Completo a [destino]** ✨\n                Incluye: Vuelo ✈️ + Hotel 🏨 (3 noches) + Actividad especial 📸\n                Precio Total: $[Precio del Paquete] USD (¡Con un [X]% de descuento!)\n
                <a href=\"[Link de Pago del Paquete Ccompleto]\" target=\"_blank\" class=\"travel-button\">¡Reserva tu Aventura a [destino] Aquí!</a>\n\n                O si prefieres por separado:\n                🛫 **Vuelo a [destino]**: $[Precio Vuelo] USD 
                <a href=\"[Link de Pago del Vuelo]\" target=\"_blank\" class=\"travel-button-small\">Reservar Vuelo</a>\n
                🏨 **Hotel en [destino] (3 noches)**: $[Precio Hotel] USD
                <a href=\"[Link de Pago del Hotel]\" target=\"_blank\" class=\"travel-button-small\">Reservar Hotel</a>\n\n                Una vez que completes la reserva, recibirás todos los detalles de tu itinerario. ¡Avísame si tienes más preguntas o necesitas algún ajuste! 😊"
        5.  **Clarity on Payment Links (Buttons)**: ALWAYS generate payment links as HTML anchor tags (`<a>`) with `target="_blank"` and the class `travel-button` for the main package and `travel-button-small` for individual items.
        6.  **Avoid Repetition**: If the orchestrator passes similar information from multiple tools, synthesize it to avoid redundancy.
        7.  **Correctness**: Double-check that all details like prices, links, and place names are accurately transcribed from the specialist agents' outputs.
            CRITICAL: Do NOT include strange formatting in your responses. Ensure all text is properly formatted. Do NOT output characters individually. Always use proper spacing.
        
        Your ultimate goal is to make the user's travel planning easy, exciting, and intuitive, seamlessly blending helpful information with relevant travel offers.
        """,
    )

@app.post("/query", response_model=Response)
async def process_query(query: ChatQuery):
    try:
        logger.debug(f"Processing query: {query.query}")
        logger.debug(f"Conversation history length: {len(query.history)}")
        
        # Format conversation history for the agent
        conversation_context = ""
        if query.history:
            conversation_context = "\nPrevious conversation:\n"
            for msg in query.history:
                conversation_context += f"{msg.role}: {msg.content}\n"
        
        # Create specialist agents
        maps_agent = create_maps_agent(conversation_context)
        payments_agent = create_payments_agent(conversation_context)
        synthesizer_agent = create_synthesizer_agent(conversation_context)
        
        # Define the orchestrator agent with specialists as tools
        orchestrator_agent = Agent(
            name="Travel Orchestrator",
            model="gpt-4o-mini",
            instructions=f"""You are an intelligent travel assistant orchestrator. Your main goal is to understand the user's needs and coordinate specialist tools to provide comprehensive and intuitive travel assistance, including information and sales of experiences or trips.
            
            {conversation_context}
            
            **Core Logic:**
            1.  **Analyze User Intent**: Carefully determine if the user is seeking information, looking to book/purchase, or both.
            2.  **Identify Key Entities**: Extract crucial information like destination names from the user's query.
            
            **Tool Usage Strategy:**
            
            *   **Scenario 1: Explicit Purchase/Booking Intent**
                *   If the user explicitly states they want to book, pay, see packages, or get prices for a trip to a [destino] (e.g., "quiero reservar un viaje a Paris", "cuánto cuesta ir a Roma", "muéstrame paquetes para Cancún"):
                    1.  Call the `create_travel_products` tool for the specified [destino].
                    2.  (Optional but recommended) Call `get_destination_info` for the [destino] to provide some context alongside the offer if not already discussed.
            
            *   **Scenario 2: Informational Query about a Specific Travel Destination**
                *   If the user asks for information about a specific [destino] (e.g., "háblame de Tokio", "qué ver en Londres", "playas en Bali"):
                    1.  Call the `get_destination_info` tool for that [destino].
                    2.  **Proactively also call** the `create_travel_products` tool for the *same* [destino] to offer relevant travel packages and experiences alongside the information. The user might be interested even if they didn't explicitly ask yet!
            
            *   **Scenario 3: General Travel Interest / Vague Query**
                *   If the user expresses general interest in travel without a specific destination (e.g., "quiero viajar", "recomiéndame un destino de playa"):
                    1.  Call `get_destination_info` to suggest a few (2-3) diverse destinations based on their vague query (e.g., suggest a beach, a city, an adventure spot).
                    2.  For each suggested destination, *briefly* mention that travel packages are available and they can ask for more details (do not call `create_travel_products` for all of them yet to avoid overwhelming the user, unless they pick one).
            
            *   **Scenario 4: Purely Informational Query (Not a travel destination)**
                *   If the query is purely informational and not directly about a travel destination (e.g., "cómo funcionan los aviones"), try to answer directly or state you specialize in travel.
            
            **Output to Synthesizer:**
            *   Pass all results from the called tools to the synthesizer agent. It is the synthesizer's job to combine these into a single, coherent response.
            
            **Example Flow (User: "Tell me about Rome")**
            1.  You identify "Rome" as the destination and the intent as informational with potential for sales.
            2.  Call `get_destination_info` for "Rome".
            3.  Call `create_travel_products` for "Rome".
            4.  The synthesizer will then combine the Rome information with the Rome travel packages.
            
            Your goal is to be helpful, intuitive, and to naturally integrate travel offers where appropriate, enhancing the user's planning experience.
            """,
            tools=[
                maps_agent.as_tool(
                    tool_name="get_destination_info",
                    tool_description="Get detailed information about travel destinations, attractions, and places using Google Maps. Use this to answer questions about what to see, do, or learn about a place."
                ),
                payments_agent.as_tool(
                    tool_name="create_travel_products",
                    tool_description="Create travel products (flights, hotels, packages) and generate payment links for specific destinations. Use this when the user wants to book, pay, see prices, or explore travel options for a place."
                )
            ],
        )
        
        trace_id = gen_trace_id()
        logger.debug(f"Generated trace ID: {trace_id}")
        
        with trace(workflow_name="Travel Assistant API", trace_id=trace_id):
            logger.debug("Starting the orchestration workflow")
            
            # First run the orchestrator to determine and use the right specialist
            orchestrator_result = await Runner.run(orchestrator_agent, query.query)
            logger.debug("Orchestrator completed")
            
            # Log the orchestrator's decision for debugging
            for item in orchestrator_result.new_items:
                if isinstance(item, MessageOutputItem):
                    text = ItemHelpers.text_message_output(item)
                    if text:
                        logger.debug(f"Orchestrator step: {text}")
            
            # Now synthesize the results into a coherent response
            synthesizer_result = await Runner.run(
                synthesizer_agent, orchestrator_result.to_input_list()
            )
            logger.debug("Synthesizer completed")
            
        return Response(
            response=synthesizer_result.final_output,
            trace_id=trace_id,
            conversation_id=query.conversation_id
        )
    except Exception as e:
        logger.error(f"Error processing query: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000) 
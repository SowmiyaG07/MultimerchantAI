from langchain.agents import create_agent
from app.tools.merchant_tool import searchmerchant
from langchain.agents.middlewware import ToolRetryMiddleware
from langchain.agents.middlewware import ToolErrorMiddleware


load_dotenv()

# api_key = os.getenv()

ollama_model = os.getenv("OLLAMA_MODEL")

ollama_base_url = os.getenv("OLLAMA_BASE_URL")


MERCHANT_AGENT_SYSTEM_PROMPT = """


"""

def on_error(exc: Exception,request : ToolCallRequest)-> str | None :
    if isinstance(exc, ValueError):
        return f"{request.tool_call['name']}, failed with {type(exec).__name__}"
    
agent = create_agent(model=ollama_model,tools=searchmerchant,system_prompt= MERCHANT_AGENT_SYSTEM_PROMPT,middleware=[ToolErrorMiddleware(on_error),ToolRetryMiddleware(max_retries = 3,backoff_factor=2.0,initil_delay = 1.0)],response_format=,state_schema=,context_schema=,checkpointer=,store=)
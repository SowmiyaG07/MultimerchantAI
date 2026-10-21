import os

from dotenv import load_dotenv
from langchain_ollama import ChatOllama
from app.tools.merchant_tool import searchmerchant


load_dotenv()

# api_key = os.getenv()

ollama_model = os.getenv("OLLAMA_MODEL")

ollama_base_url = os.getenv("OLLAMA_BASE_URL")

model = ChatOllama(model=ollama_model, base_url=ollama_base_url,
                   temperature=0.4, max_tokens=1000, timeout=10, max_retries=10,)


conversation = [
    {"role": "system", "content": "you are helpful Ai assiatnt that translate english to german"},
    {"role": "user", "content": "Translate: Hello How are you?"},
    {"role": "assistant", "content": "Hallo, wie geht es dir?"},
    {"role": "user", "content": "Translate:Hello Good Morning Baby"},
]


model_with_tools = model.bind_tools([searchmerchant])


responses = model_with_tools.invoke("dreamlandbbq")

for tool_call in responses.tool_calls:
    print(f"Tool: {tool_call['name']}")
    print(f"Tool: {tool_call['args']}")
    if tool_call["name"] == searchmerchant.name:
        result = searchmerchant.invoke(tool_call["args"])
        print("Merchant result:", result)

    else:
        print("Model response:", responses.content)

# for response in responses:
    print(responses)

# for chunk in model.stream("Why do parrots have colorful feathers?"):
    # print(chunk.text, end="|", flush=True)


# print(response)

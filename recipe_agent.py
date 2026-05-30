from typing import Any

import tavily
from langchain.agents import create_agent
from langchain.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from dotenv import load_dotenv
from tavily import TavilyClient
from typing import Dict, Any


load_dotenv(dotenv_path="../.env", override=True)
tavily_client = TavilyClient()

@tool
def recipe_agent(items: str) -> dict[str, Any]:
    """ Fetch the recipes from the Web for the given list of items """
    return tavily_client.search(items)

config = {"configurable": {"thread_id": "3"}}

system_prompt = """
You are my personal chef. The user will give you a list of ingredients they have left over in their house.

Using the web search tool, search the recipes that can be made with the ingredients they have.

Return recipe suggestions and eventually the recipe instructions to the user, if requested.
"""

agent = create_agent(
    model="gpt-5-nano",
    tools=[recipe_agent],
    system_prompt=system_prompt
)
graph_with_memory = agent.with_config(checkpointer=InMemorySaver())

result = graph_with_memory.invoke({"messages": [{"role": "user",
                                  "content": "Hello! these are the items I have in my pantry. Onions, tomatoes, potatoes, salt, chilli powder, rice"}]}, config)

print(result["messages"][-1].content)

result = agent.invoke({"messages": [{"role": "user","content":"I have hing,Chicken, mint, cilantro and turmeric"}]}, config)
print(result["messages"][-1].content)


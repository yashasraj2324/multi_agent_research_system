import asyncio
from research_system.core.llm import get_model
from pydantic_ai import Agent

async def main():
    model = get_model()
    agent = Agent(model)
    result = await agent.run('say hello')
    print(result.data)

asyncio.run(main())

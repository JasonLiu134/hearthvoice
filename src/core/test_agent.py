
"""Run Hearth as an interactive terminal conversation."""

import asyncio

from agent import HearthAgent

PREFERRED_NAME = "Julie"
FULL_NAME = "Julie Smith"
NOTES = "Enjoys gardening and talking about her family."

USER = {
    "preferred_name": PREFERRED_NAME,
    "full_name": FULL_NAME,
    "notes": NOTES,
}


async def main():
    agent = HearthAgent()
    history = []

    print(f"\nHearth: {agent.opening_line(USER)}")
    print("\nCommands: /summary, /quit\n")

    while True:
        try:
            message = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not message:
            continue

        if message.lower() == "/quit":
            print("Goodbye!")
            break

        if message.lower() == "/summary":
            if not history:
                print("No conversation yet.\n")
                continue

            try:
                summary = await agent.summarize(history)
                print(f"\nFamily summary: {summary}\n")
            except Exception as exc:
                print(f"\nSummary error: {exc}\n")
            continue

        history.append({"role": "user", "content": message})

        print("Hearth: ", end="", flush=True)
        response = ""

        try:
            async for chunk in agent.reply_stream(history, USER):
                print(chunk, end="", flush=True)
                response += chunk

            print("\n")
            history.append({
                "role": "assistant",
                "content": response,
            })

        except Exception as exc:
            history.pop()
            print(f"\nAPI error: {exc}\n")


if __name__ == "__main__":
    asyncio.run(main())

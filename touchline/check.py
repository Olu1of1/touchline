"""Check the language model is reachable and answering through the Agent Framework workflow.

    python -m touchline.check

Asks Ask Touchline one question about M059 and prints what came back. Run it after filling in .env.
"""

from .agents import AskTouchline


def main():
    r = AskTouchline().ask("M059", 1600, "What is the score?")
    print("model:   ", r["backend"])
    print("verdict: ", r["verdict"])
    print("answer:  ", r["answer"])
    if r["problems"]:
        print("problems:", r["problems"])
    if r["backend"] == "offline router":
        print("\nNo model configured: fill in AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY and AZURE_OPENAI_DEPLOYMENT in .env.")
    elif r["verdict"] == "accepted":
        print("\nWorking. Next: python -m touchline.prebuild")
    else:
        print("\nThe model call didn't succeed; see problems above.")


if __name__ == "__main__":
    main()

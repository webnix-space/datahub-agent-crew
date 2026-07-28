"""
The agent loop is 100% reactive — nobody talks until someone sends the
first message. Run this to kick off one scan cycle:

    python seed_scan.py

It drops a message addressed to the Investigator into the local bus,
which starts: Investigator -> Analyst -> Strategist -> Regulatory ->
Codeband -> (loop trigger, stops).

Run orchestrator.py in one terminal/process, run this in another once
you see "Started: DataHub Investigator" in the logs.
"""
import os

from dotenv import load_dotenv

from base_agent import ROOM_ID
from local_client import LocalClient

load_dotenv()


def main():
    client = LocalClient("Human Operator")
    participants = client.get_participants(ROOM_ID)
    investigator = next((p for p in participants if p["name"] == "DataHub Investigator"), None)
    if not investigator:
        raise SystemExit(f"No Investigator agent found in participants: {participants}")

    client.send_message(
        ROOM_ID,
        "@DataHub Investigator please scan for governance gaps.",
        mentions=[investigator],
    )
    print(f"Seeded scan request, room={ROOM_ID}. Watch orchestrator.py logs.")


if __name__ == "__main__":
    main()

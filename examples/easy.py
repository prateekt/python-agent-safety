"""The easy way — start here.

    python examples/easy.py

Two ideas: mark a function with @tool, then run it inside a safely(...) block that
says what's allowed. That's it.
"""

from agent_safety import safely, tool
from agent_safety.exceptions import ExplanationRequired, PermissionDenied


@tool
def read_file(name):
    # Pretend this file holds a secret email address.
    return f"[{name}] from jane@secret.com — hello!"


@tool
def delete_everything():
    return "everything is gone"


print("1) Allow reading, and hide secrets in the result:")
with safely(allow="read_file", hide_secrets=True):
    print("  ", read_file("diary.txt"))   # the email is scrubbed

print("\n2) We only allowed reading — deleting is blocked:")
with safely(allow="read_file"):
    try:
        delete_everything()
    except PermissionDenied:
        print("   delete_everything was not allowed. Phew.")

print("\n3) Give a small budget — only 2 calls:")
with safely(allow="read_file", calls=2):
    print("  ", read_file("a.txt"))
    print("  ", read_file("b.txt"))
    try:
        read_file("c.txt")
    except Exception as e:
        print("   out of budget:", e)

print("\n4) Make the agent say WHY before it acts:")
with safely(allow="delete_everything", explain=True):
    print("  ", delete_everything(rationale="The user asked me to wipe the test folder"))
    try:
        delete_everything()                # no reason given
    except ExplanationRequired:
        print("   blocked: it must explain itself first.")

print("\n5) Watch everything that happens:")
with safely(allow="read_file", log=True):
    read_file("watch-me.txt")

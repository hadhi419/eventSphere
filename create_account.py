from getpass import getpass
import argparse

from auth import Credentials, create_user
from main import engine, initialize

parser = argparse.ArgumentParser()
parser.add_argument("role", choices=["admin", "staff"])
parser.add_argument("username")
args = parser.parse_args()
credentials = Credentials(username=args.username, password=getpass("Password (12+ characters): "))
initialize()
with engine.begin() as db:
    if not create_user(db, credentials, args.role):
        parser.exit(1, "Username already exists.\n")
print(f"Created {args.role} account {credentials.username.lower()}.")

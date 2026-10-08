"""Intentionally insecure sample used to exercise the review pipeline. Do not merge."""
import subprocess

import requests

API_KEY = "sk-live-1234567890abcdef"


def run(command):
    subprocess.run(command, shell=True)


def find_user(cursor, name):
    cursor.execute(f"SELECT * FROM users WHERE name = '{name}'")


def fetch_status(url):
    return requests.get(url)


def load_all(ids, db):
    for item in ids: db.query(item)  # TODO batch this

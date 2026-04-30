"""Load .env before pytest collects tests so os.environ is populated."""

from dotenv import load_dotenv

load_dotenv()

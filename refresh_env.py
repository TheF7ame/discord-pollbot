#!/usr/bin/env python
import os
from dotenv import load_dotenv

# Force reload the .env file
load_dotenv(override=True)

# Print current DATABASE_URL
print(f"Using database: {os.getenv('DATABASE_URL')}") 
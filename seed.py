import datetime
from pymongo import MongoClient
from dotenv import load_dotenv
import os
import bcrypt

load_dotenv()
client = MongoClient(os.getenv("MONGO_URI"))
db = client.get_database(os.getenv("DB_NAME", "SustainEatsDB"))

users = db.users
recipes = db.recipes

# Create test user if not exists
email = "testuser@example.com"
if users.find_one({"email": email}) is None:
    pwd = bcrypt.hashpw("Test@123".encode("utf-8"), bcrypt.gensalt())
    uid = users.insert_one({
        "name": "Test User",
        "email": email,
        "password": pwd,
        "role": "user",
        "created_at": datetime.datetime.utcnow()
    }).inserted_id
    print("Created test user:", uid)
else:
    print("Test user already exists.")

# sample recipes
sample = [
    {
        "name": "Tomato Rice",
        "sustainability_score": 10,
        "ingredients": [
            {"item_name": "tomato"},
            {"item_name": "rice"},
            {"item_name": "onion"}
        ]
    },
    {
        "name": "Potato Curry",
        "sustainability_score": 8,
        "ingredients": [
            {"item_name": "potato"},
            {"item_name": "onion"},
            {"item_name": "spices"}
        ]
    }
]

for r in sample:
    if recipes.find_one({"name": r["name"]}) is None:
        recipes.insert_one(r)
        print("Inserted recipe:", r["name"])
    else:
        print("Recipe exists:", r["name"])

from flask import Flask, request, jsonify
from flask_cors import CORS
from pymongo import MongoClient
import os
import sys
from datetime import datetime
from dotenv import load_dotenv
from bson.json_util import dumps
import bcrypt

load_dotenv() 

app = Flask(__name__)
CORS(app)

# --- Database Connection ---
try:
    mongo_uri = os.getenv("MONGO_URI") or "mongodb://localhost:27017/"
    db_name = os.getenv("DB_NAME") or "SustainEatsDB"
    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
    db = client[db_name]
    
    users_collection = db["users"]
    recipes_collection = db["global_recipes"]
    feedback_collection = db["feedback"]
    waste_collection = db["waste_logs"]
    
    client.admin.command('ping')
    print(f"✅ Connected to MongoDB: {db_name}")
except Exception as e:
    print(f"❌ DB Connection Failed: {e}")
    sys.exit(1)

# ---------------- HELPER ---------------- #
def calculate_nutrition(ingredients):
    return {
        "calories": len(ingredients) * 150,
        "protein": f"{len(ingredients)*5}g",
        "fat": "Low"
    }

def is_admin(email):
    user = users_collection.find_one({"email": email})
    return user and user.get("role") == "admin"

@app.route('/')
def health_check():
    return jsonify({"status": "online"}), 200

# ---------------- AUTH ---------------- #
@app.route('/register', methods=['POST'])
def register():
    data = request.json
    email = data.get("email").lower().strip()

    if users_collection.find_one({"email": email}):
        return jsonify({"message": "User exists"}), 400

    hashed = bcrypt.hashpw(data["password"].encode(), bcrypt.gensalt()).decode()

    users_collection.insert_one({
        "name": data["name"],
        "email": email,
        "password": hashed,
        "role": "user",
        "inventory": [],
        "shoppingList": [],
        "ecoScore": 0,
        "badges": [],
        "itemsSaved": 0,
        "age": None,
        "height": None,
        "weight": None,
        "bmi": None,
        "healthCondition": "None",
        "dietPreference": "Veg"
    })

    return jsonify({"message": "Registered"}), 201

@app.route('/login', methods=['POST'])
def login():
    data = request.json
    email = data.get("email").lower().strip()
    user = users_collection.find_one({"email": email})

    if not user:
        return jsonify({"message": "User not found"}), 404

    if bcrypt.checkpw(data["password"].encode(), user["password"].encode()):
        return jsonify({
            "name": user["name"],
            "email": user["email"],
            "role": user.get("role", "user")
        })
    
    return jsonify({"message": "Invalid password"}), 401

# ---------------- PROFILE ---------------- #
@app.route('/update-profile', methods=['POST'])
def update_profile():
    data = request.json
    users_collection.update_one(
        {"email": data["email"]},
        {"$set": data}
    )
    return jsonify({"message": "Updated"})

@app.route('/profile/<email>')
def profile(email):
    user = users_collection.find_one({"email": email}, {"_id": 0, "password": 0})
    return jsonify(user or {})

# ---------------- INVENTORY ---------------- #
@app.route('/inventory/<email>')
def get_inventory(email):
    user = users_collection.find_one({"email": email})
    return jsonify(user.get("inventory", []) if user else [])

@app.route('/inventory', methods=['POST'])
def add_inventory():
    data = request.json
    item = {
        "name": data["name"],
        "quantity": data["quantity"],
        "expiry": data["expiry"],
        "storage": data.get("storage", "Fridge"),
        "added_at": datetime.now().strftime("%Y-%m-%d")
    }

    users_collection.update_one(
        {"email": data["email"]},
        {"$push": {"inventory": item}}
    )

    return jsonify({"message": "Added"})

@app.route('/inventory/delete', methods=['POST'])
def delete_item():
    data = request.json
    email = data["email"]

    if data.get("reason") == "waste":
        waste_collection.insert_one({
            "email": email,
            "item_name": data["name"],
            "quantity": data.get("quantity", 1),
            "waste_date": datetime.now().strftime("%Y-%m-%d")
        })
        users_collection.update_one({"email": email}, {"$inc": {"ecoScore": -2}})
    elif data.get("reason") == "consumed":
        users_collection.update_one({"email": email}, {"$inc": {"ecoScore": 5, "itemsSaved": 1}})

    users_collection.update_one(
        {"email": email},
        {"$pull": {"inventory": {"name": data["name"]}}}
    )
    
    # Check badges
    user = users_collection.find_one({"email": email})
    if user:
        new_badges = user.get("badges", [])
        saved = user.get("itemsSaved", 0)
        eco = user.get("ecoScore", 0)
        
        if saved >= 10 and "Zero-Waste Hero" not in new_badges:
            new_badges.append("Zero-Waste Hero")
        if eco >= 50 and "Pantry Master" not in new_badges:
            new_badges.append("Pantry Master")
            
        if len(new_badges) > len(user.get("badges", [])):
            users_collection.update_one({"email": email}, {"$set": {"badges": new_badges}})

    return jsonify({"message": "Removed"})

# ---------------- SHOPPING LIST & STATS ---------------- #
@app.route('/shopping-list/<email>')
def get_shopping_list(email):
    user = users_collection.find_one({"email": email})
    return jsonify(user.get("shoppingList", []) if user else [])

@app.route('/shopping-list', methods=['POST'])
def update_shopping_list():
    data = request.json
    action = data.get("action")
    items = data.get("items", [])
    
    if action == "add":
        for item in items:
            users_collection.update_one({"email": data["email"]}, {"$addToSet": {"shoppingList": item}})
    elif action == "remove":
        for item in items:
            users_collection.update_one({"email": data["email"]}, {"$pull": {"shoppingList": item}})
            
    return jsonify({"message": "Shopping list updated"})

@app.route('/user-stats/<email>')
def get_user_stats(email):
    user = users_collection.find_one({"email": email})
    if not user:
        return jsonify({"message": "User not found"}), 404
        
    wastes = list(waste_collection.find({"email": email}))
    total_wasted = sum(int(w.get("quantity", 1)) for w in wastes)
    cost_lost = total_wasted * 3.50
    co2_emitted = total_wasted * 2.5
    
    return jsonify({
        "ecoScore": user.get("ecoScore", 0),
        "badges": user.get("badges", []),
        "itemsSaved": user.get("itemsSaved", 0),
        "moneyLost": f"${cost_lost:.2f}",
        "co2Emitted": f"{co2_emitted:.1f} kg",
        "totalWasted": total_wasted
    })

# ---------------- RECIPES ---------------- #
@app.route('/suggest-recipes/<email>')
def suggest(email):
    user = users_collection.find_one({"email": email})
    if not user:
        return jsonify([])

    fridge = [i["name"].lower() for i in user.get("inventory", [])]

    recipes = list(recipes_collection.find({}, {"_id": 0}))
    result = []

    for r in recipes:
        ing = [i.lower() for i in r.get("ingredients", [])]
        match = [i for i in ing if i in fridge]

        if match:
            result.append({
                "recipe_name": r["name"],
                "matched": match,
                "missing": [i for i in ing if i not in match],
                "nutrition": calculate_nutrition(ing)
            })

    return jsonify(result[:10])


# ---------------- ADMIN ---------------- #
@app.route('/admin/users')
def admin_get_users():
    users = list(users_collection.find({"role": {"$ne": "admin"}}, {"_id": 0, "password": 0}))
    return jsonify(users)

@app.route('/admin/delete-user/<email>', methods=['DELETE'])
def admin_delete_user(email):
    result = users_collection.delete_one({"email": email})
    if result.deleted_count:
        return jsonify({"message": "User deleted successfully"}), 200
    return jsonify({"message": "User not found"}), 404

@app.route('/admin/waste-reports')
def admin_get_waste_reports():
    reports = list(waste_collection.find({}, {"_id": 0}))
    return jsonify(reports)

@app.route('/admin/stats')
def admin_get_stats():
    total_users = users_collection.count_documents({"role": {"$ne": "admin"}})
    total_waste = waste_collection.count_documents({})
    total_recipes = recipes_collection.count_documents({})
    return jsonify({
        "totalUsers": total_users,
        "totalWasteItems": total_waste,
        "totalRecipes": total_recipes
    })

# ---------------- RUN ---------------- #
if __name__ == '__main__':
    app.run(debug=True)
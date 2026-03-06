from flask import Flask, request, jsonify
from flask_cors import CORS
from pymongo import MongoClient
import os
import sys
from datetime import datetime
from dotenv import load_dotenv
from bson.json_util import dumps
import json

load_dotenv() 

app = Flask(__name__)
# Adjust origins if your frontend port changes (Vite usually uses 5173)
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
    waste_collection = db["waste_logs"] # Collection for Admin Waste Reports
    
    client.admin.command('ping')
    print(f"✅ Successfully connected to MongoDB: {db_name}")
except Exception as e:
    print(f"❌ CRITICAL: Could not connect to MongoDB. Reason: {e}")
    sys.exit(1)

# --- Helper Functions ---
def calculate_nutrition(ingredients):
    calories = len(ingredients) * 150 
    protein = len(ingredients) * 5
    return {"calories": calories, "protein": f"{protein}g", "fat": "Low"}

@app.route('/')
def health_check():
    return jsonify({"status": "online", "database": db_name}), 200

# --- USER AUTH ---
@app.route('/register', methods=['POST'])
def register():
    data = request.json
    email = data.get('email')
    if users_collection.find_one({"email": email}):
        return jsonify({"message": "User already exists"}), 400
    
    users_collection.insert_one({
        "name": data.get('name'),
        "email": email,
        "password": data.get('password'), 
        "role": 'admin' if 'admin' in email.lower() else 'user',
        "inventory": [],
        "age": None, "height": None, "weight": None, "bmi": None,
        "healthCondition": "None", "dietPreference": "Veg" 
    })
    return jsonify({"message": "User registered successfully"}), 201

@app.route('/login', methods=['POST'])
def login():
    try:
        data = request.get_json()

        email = data.get("email")
        password = data.get("password")

        user = users_collection.find_one({"email": email})

        if not user:
            return jsonify({"message": "User not found"}), 404

        # Check hashed password
        if bcrypt.checkpw(password.encode('utf-8'), user["password"]):

            return jsonify({
                "message": "Login successful",
                "name": user.get("name", "User"),
                "role": user.get("role", "user")
            })

        return jsonify({"message": "Invalid password"}), 401

    except Exception as e:
        print("Login error:", e)
        return jsonify({"message": "Server error"}), 500
# --- PROFILE MANAGEMENT ---
@app.route('/update-profile', methods=['POST'])
def update_profile():
    data = request.json
    email = data.get('email')
    update_data = {
        "age": data.get('age'),
        "height": data.get('height'),
        "weight": data.get('weight'),
        "healthCondition": data.get('healthCondition'),
        "dietPreference": data.get('dietPreference'),
        "bmi": data.get('bmi')
    }
    users_collection.update_one({"email": email}, {"$set": update_data})
    return jsonify({"message": "Profile updated successfully!"}), 200

@app.route('/profile/<email>', methods=['GET'])
def get_profile(email):
    user = users_collection.find_one({"email": email}, {"_id": 0, "password": 0})
    if user:
        return jsonify(user), 200
    return jsonify({"message": "User not found"}), 404

# --- INVENTORY & WASTE TRACKING ---
@app.route('/inventory/<email>', methods=['GET'])
def get_inventory(email):
    user = users_collection.find_one({"email": email})
    return jsonify(user.get('inventory', []) if user else []), 200

@app.route('/inventory', methods=['POST'])
def add_inventory():
    data = request.json
    email = data.get('email')
    new_item = {
        "name": data.get('name'), 
        "quantity": data.get('quantity'), 
        "expiry": data.get('expiry'),
        "added_at": datetime.now().strftime("%Y-%m-%d")
    }
    users_collection.update_one({"email": email}, {"$push": {"inventory": new_item}})
    return jsonify({"message": "Item added!"}), 201

@app.route('/inventory/delete', methods=['POST'])
def delete_item():
    data = request.json
    email = data.get('email')
    item_name = data.get('name')
    reason = data.get('reason', 'consumed') # 'waste' or 'consumed'

    if reason == 'waste':
        waste_collection.insert_one({
            "email": email,
            "item_name": item_name,
            "waste_date": datetime.now().strftime("%Y-%m-%d"),
            "quantity": data.get('quantity', 1)
        })

    users_collection.update_one({"email": email}, {"$pull": {"inventory": {"name": item_name}}})
    return jsonify({"message": f"Item {reason} processed!"}), 200

# --- SMART RECIPES ---
@app.route('/suggest-recipes/<email>', methods=['GET'])
def suggest_recipes(email):
    user = users_collection.find_one({"email": email})
    if not user: return jsonify([]), 200

    user_diet = user.get('dietPreference', 'Veg')
    fridge_ingredients = [item['name'].lower().strip() for item in user.get('inventory', [])]

    query = {}
    if user_diet == "Veg":
        query["type"] = "Veg"
    
    db_recipes = list(recipes_collection.find(query, {'_id': 0}))
    suggestions = []

    for recipe in db_recipes:
        recipe_ings = [ing.lower().strip() for ing in recipe.get('ingredients', [])]
        matches = [ing for ing in recipe_ings if any(ing in item for item in fridge_ingredients)]
        
        if matches:
            suggestions.append({
                "recipe_name": recipe.get('name'),
                "diet_type": recipe.get('type'),
                "matched_ingredients": matches,
                "missing_ingredients": [ing for ing in recipe_ings if ing not in matches],
                "sustainability_score": recipe.get('carbon_score', 5),
                "instructions": recipe.get('instructions', []),
                "nutrition": calculate_nutrition(recipe_ings)
            })

    suggestions = sorted(suggestions, key=lambda x: len(x['matched_ingredients']), reverse=True)
    return jsonify(suggestions[:10]), 200

# --- FEEDBACK ---
@app.route('/feedback', methods=['POST'])
def save_feedback():
    data = request.json
    feedback_collection.insert_one({
        "email": data.get('email'),
        "recipe_name": data.get('recipe_name'),
        "rating": data.get('rating'),
        "comment": data.get('comment', ""),
        "timestamp": datetime.now()
    })
    return jsonify({"message": "Feedback received!"}), 201

# --- ADMIN DASHBOARD ROUTES ---
@app.route('/admin/users', methods=['GET'])
def get_all_users():
    users = list(users_collection.find({}, {"password": 0, "_id": 0}))
    return dumps(users)

@app.route('/admin/post-recipe', methods=['POST'])
def post_recipe():
    data = request.json
    new_recipe = {
        "name": data.get('name'),
        "type": data.get('type', 'Veg'),
        "ingredients": data.get('ingredients', []),
        "instructions": data.get('instructions', []),
        "carbon_score": int(data.get('ecoScore', 5)),
        "created_at": datetime.now()
    }
    recipes_collection.insert_one(new_recipe)
    return jsonify({"message": "Global Recipe published!"}), 201

@app.route('/admin/waste-reports', methods=['GET'])
def get_waste_reports():
    reports = list(waste_collection.find({}, {"_id": 0}))
    return dumps(reports)

@app.route('/admin/delete-user/<email>', methods=['DELETE'])
def delete_user(email):
    users_collection.delete_one({"email": email})
    # Optional: Clear their waste logs too
    # waste_collection.delete_many({"email": email})
    return jsonify({"message": f"User {email} deleted successfully"}), 200

# --- ADMIN: GENERATE INDIVIDUAL REPORT (UPDATED FOR MONGODB) ---
@app.route('/admin/generate-report/<email>', methods=['GET'])
def generate_report(email):
    try:
        user_waste_logs = list(waste_collection.find({"email": email}, {"_id": 0}))
        return jsonify(user_waste_logs), 200
    except Exception as e:
        print(f"Error generating report for {email}: {e}")
        return jsonify({"message": "Internal Server Error"}), 500


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
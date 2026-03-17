from flask import Flask, request, jsonify
from flask_cors import CORS
from pymongo import MongoClient
import os
import sys
from datetime import datetime, timedelta
from dotenv import load_dotenv
from bson.json_util import dumps
import bcrypt
import jwt
from functools import wraps
import google.generativeai as genai

load_dotenv() 

app = Flask(__name__)
# Check for both SECRET_KEY and JWT_SECRET for backward compatibility with .env
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY') or os.getenv('JWT_SECRET') or 'super-secret-default-key-1234'
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)

# --- Configure Gemini API ---
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

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

# ---------------- AUTH DECORATOR ---------------- #
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if request.method == 'OPTIONS':
            return jsonify({}), 200
        token = request.headers.get("Authorization")
        if not token:
            return jsonify({"message": "Token is missing"}), 401
        
        try:
            if token.startswith("Bearer "):
                token = token.split(" ")[1]
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=["HS256"])
            current_user = users_collection.find_one({"email": data["email"]})
            if not current_user:
                return jsonify({"message": "Invalid token"}), 401
        except jwt.ExpiredSignatureError:
            return jsonify({"message": "Token has expired"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"message": "Token is invalid"}), 401
            
        kwargs['current_user'] = current_user
        return f(*args, **kwargs)
    return decorated

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
        token = jwt.encode({
            'email': user['email'],
            'exp': datetime.utcnow() + timedelta(days=7)
        }, app.config['SECRET_KEY'], algorithm="HS256")
        
        return jsonify({
            "name": user["name"],
            "email": user["email"],
            "role": user.get("role", "user"),
            "token": token
        })
    
    return jsonify({"message": "Invalid password"}), 401

# ---------------- PROFILE ---------------- #
@app.route('/update-profile', methods=['POST'])
@token_required
def update_profile(current_user):
    data = request.json
    users_collection.update_one(
        {"email": current_user["email"]},
        {"$set": data}
    )
    return jsonify({"message": "Updated"})

@app.route('/profile/<email>')
@token_required
def profile(email, current_user):
    user = users_collection.find_one({"email": email}, {"_id": 0, "password": 0})
    return jsonify(user or {})

# ---------------- INVENTORY ---------------- #
@app.route('/inventory/<email>')
@token_required
def get_inventory(email, current_user):
    user = users_collection.find_one({"email": email})
    return jsonify(user.get("inventory", []) if user else [])

@app.route('/inventory', methods=['POST'])
@token_required
def add_inventory(current_user):
    data = request.json
    email = data.get("email") or current_user["email"]
    item = {
        "name": data["name"],
        "quantity": data["quantity"],
        "expiry": data["expiry"],
        "storage": data.get("storage", "Fridge"),
        "added_at": datetime.now().strftime("%Y-%m-%d")
    }

    users_collection.update_one(
        {"email": email},
        {"$push": {"inventory": item}}
    )

    return jsonify({"message": "Added"})

@app.route('/inventory/delete', methods=['POST'])
@token_required
def delete_item(current_user):
    data = request.json
    email = data.get("email") or current_user["email"]

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
@token_required
def get_shopping_list(email, current_user):
    user = users_collection.find_one({"email": email})
    return jsonify(user.get("shoppingList", []) if user else [])

@app.route('/shopping-list', methods=['POST'])
@token_required
def update_shopping_list(current_user):
    data = request.json
    email = data.get("email") or current_user["email"]
    action = data.get("action")
    items = data.get("items", [])
    
    if action == "add":
        for item in items:
            users_collection.update_one({"email": email}, {"$addToSet": {"shoppingList": item}})
    elif action == "remove":
        for item in items:
            users_collection.update_one({"email": email}, {"$pull": {"shoppingList": item}})
            
    return jsonify({"message": "Shopping list updated"})

@app.route('/user-stats/<email>')
@token_required
def get_user_stats(email, current_user):
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
@token_required
def suggest(email, current_user):
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

@app.route('/feedback', methods=['POST'])
@token_required
def submit_feedback(current_user):
    data = request.json
    email = data.get("email") or current_user["email"]
    feedback_collection.insert_one({
        "email": email,
        "recipe_name": data.get("recipe_name"),
        "rating": data.get("rating"),
        "comments": data.get("comments"),
        "submitted_at": datetime.now().strftime("%Y-%m-%d")
    })
    return jsonify({"message": "Feedback received"}), 201

@app.route('/generate-ai-recipe/<email>')
@token_required
def generate_ai_recipe(email, current_user):
    fridge_items = [i["name"] for i in current_user.get("inventory", [])]
    
    if not fridge_items:
        return jsonify({"error": "Your fridge is empty! Add items first."}), 400
        
    prompt = f"I am building a web app to reduce food waste. The user has these ingredients in their digital fridge: {', '.join(fridge_items)}. Create a sustainable and delicious recipe using as many of these ingredients as possible. Keep it concise. Include: Name, Match (ingredients used from the list), Missing (pantry staples I need), Instructions, and a sustainability score out of 10."
    
    try:
        # Try the newer flash model first
        try:
            model = genai.GenerativeModel("gemini-1.5-flash")
            response = model.generate_content(prompt)
        except Exception as flash_err:
            print(f"Flash model failed: {flash_err}. Falling back to gemini-pro.")
            # Fallback to the classic pro model
            model = genai.GenerativeModel("gemini-pro")
            response = model.generate_content(prompt)
            
        return jsonify({"recipe_text": response.text})
    except Exception as e:
        return jsonify({"error": f"Failed to generate recipe. Details: {str(e)}"}), 500


# ---------------- ADMIN ---------------- #
def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        current_user = kwargs.get('current_user')
        if not current_user or current_user.get("role") != "admin":
            return jsonify({"message": "Admin access required"}), 403
        return f(*args, **kwargs)
    return decorated

@app.route('/admin/users')
@token_required
@admin_required
def admin_get_users(current_user):
    users = list(users_collection.find({"role": {"$ne": "admin"}}, {"_id": 0, "password": 0}))
    return jsonify(users)

@app.route('/admin/delete-user/<email>', methods=['DELETE'])
@token_required
@admin_required
def admin_delete_user(email, current_user):
    result = users_collection.delete_one({"email": email})
    if result.deleted_count:
        return jsonify({"message": "User deleted successfully"}), 200
    return jsonify({"message": "User not found"}), 404

@app.route('/admin/waste-reports')
@token_required
@admin_required
def admin_get_waste_reports(current_user):
    reports = list(waste_collection.find({}, {"_id": 0}))
    return jsonify(reports)

@app.route('/admin/stats')
@token_required
@admin_required
def admin_get_stats(current_user):
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
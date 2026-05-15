from dotenv import load_dotenv
load_dotenv()

import os
import uuid
import logging
import bcrypt
import jwt
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, APIRouter, HTTPException, Depends, Request, Response
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
from pydantic import BaseModel, EmailStr, Field

ROOT_DIR = Path(__file__).parent
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

JWT_ALGORITHM = "HS256"
ACCESS_TTL_MIN = 60 * 24 * 7  # 7 days
PROFILE_ID_LEGACY = "default"

# ===== Default Constants (used to seed a new build's settings) =====
DEFAULT_STAT_XP = {
    "pts": 10, "reb": 8, "ast": 12, "stl": 12, "blk": 12,
    "tov": -8, "fgm": 0, "tpm": 0, "ftm": 0, "fouls": 0,
}
DEFAULT_BONUS_XP = {
    "win": 100, "loss": 25, "player_of_game": 50,
    "double_double": 40, "triple_double": 100, "offseason": 3000,
}
DIFFICULTY_MULTIPLIERS = {
    "Pro": 1.0, "All-Star": 1.2, "Superstar": 1.5, "Hall of Fame": 2.0,
}

# rating tiers: 1-25, 26-50, 51-75, 76-85, 86-90, 91-99
RATING_TIERS = ["1-25", "26-50", "51-75", "76-85", "86-90", "91-99"]
ATTRIBUTE_CATEGORIES = ["Finishing", "Shooting", "Playmaking", "Defense", "Rebounding", "Physicals"]
BADGE_TIER_ORDER = ["None", "Bronze", "Silver", "Gold", "HOF", "Legend"]
DEFAULT_BADGE_TIER_COST = {"None": 0, "Bronze": 100, "Silver": 200, "Gold": 300, "HOF": 400, "Legend": 500}

# Per-category attribute upgrade costs (per-tier)
DEFAULT_ATTR_COSTS = {
    "Finishing":  {"1-25": 50,  "26-50": 50,  "51-75": 75,  "76-85": 100, "86-90": 225, "91-99": 500},
    "Shooting":   {"1-25": 50,  "26-50": 50,  "51-75": 75,  "76-85": 150, "86-90": 250, "91-99": 600},
    "Playmaking": {"1-25": 50,  "26-50": 50,  "51-75": 100, "76-85": 150, "86-90": 250, "91-99": 600},
    "Defense":    {"1-25": 50,  "26-50": 50,  "51-75": 75,  "76-85": 100, "86-90": 225, "91-99": 500},
    "Rebounding": {"1-25": 50,  "26-50": 50,  "51-75": 75,  "76-85": 100, "86-90": 225, "91-99": 500},
    "Physicals":  {"1-25": 100, "26-50": 100, "51-75": 225, "76-85": 500, "86-90": 750, "91-99": 1000},
}

def default_attr_costs():
    return {cat: dict(tiers) for cat, tiers in DEFAULT_ATTR_COSTS.items()}

# Starting-attribute archetypes (Speed/Agility are height-based; computed at apply time)
ARCHETYPES = {
    "Sharpshooter": {
        "Close Shot": 70, "Driving Layup": 70, "Driving Dunk": 55, "Standing Dunk": 50, "Post Control": 55,
        "Offensive Rebound": 50, "Defensive Rebound": 58,
        "Mid-Range Shot": 82, "Three-Point Shot": 85, "Free Throw": 80,
        "Pass Accuracy": 72, "Ball Handle": 75, "Speed With Ball": 70,
        "Interior Defense": 55, "Perimeter Defense": 70, "Steal": 70, "Block": 50,
        "Strength": 60, "Vertical": 60,
    },
    "Slasher": {
        "Close Shot": 76, "Driving Layup": 85, "Driving Dunk": 85, "Standing Dunk": 72, "Post Control": 60,
        "Offensive Rebound": 60, "Defensive Rebound": 65,
        "Mid-Range Shot": 68, "Three-Point Shot": 60, "Free Throw": 70,
        "Pass Accuracy": 65, "Ball Handle": 74, "Speed With Ball": 78,
        "Interior Defense": 60, "Perimeter Defense": 70, "Steal": 65, "Block": 65,
        "Strength": 75, "Vertical": 83,
    },
    "Playmaker": {
        "Close Shot": 70, "Driving Layup": 75, "Driving Dunk": 60, "Standing Dunk": 50, "Post Control": 55,
        "Offensive Rebound": 55, "Defensive Rebound": 60,
        "Mid-Range Shot": 72, "Three-Point Shot": 74, "Free Throw": 78,
        "Pass Accuracy": 85, "Ball Handle": 85, "Speed With Ball": 82,
        "Interior Defense": 55, "Perimeter Defense": 70, "Steal": 70, "Block": 50,
        "Strength": 60, "Vertical": 65,
    },
    "Lock": {
        "Close Shot": 65, "Driving Layup": 72, "Driving Dunk": 65, "Standing Dunk": 60, "Post Control": 58,
        "Offensive Rebound": 65, "Defensive Rebound": 74,
        "Mid-Range Shot": 62, "Three-Point Shot": 58, "Free Throw": 65,
        "Pass Accuracy": 70, "Ball Handle": 68, "Speed With Ball": 70,
        "Interior Defense": 75, "Perimeter Defense": 85, "Steal": 85, "Block": 80,
        "Strength": 77, "Vertical": 78,
    },
}

HEIGHT_BASED = {
    # bucket -> {archetype -> level}; applies to both Speed and Agility
    "short":  {"Sharpshooter": 75, "Slasher": 80, "Playmaker": 85, "Lock": 80},
    "medium": {"Sharpshooter": 70, "Slasher": 75, "Playmaker": 80, "Lock": 75},
    "tall":   {"Sharpshooter": 65, "Slasher": 70, "Playmaker": 75, "Lock": 70},
}
HEIGHT_BUCKET_LABELS = {"short": "5'5\" – 6'4\"", "medium": "6'5\" – 6'9\"", "tall": "6'10\" – 7'7\""}

def height_to_inches(h: str) -> int:
    try:
        s = (h or "").strip().replace('"', "")
        if "'" in s:
            ft_s, in_s = s.split("'", 1)
            return int(ft_s) * 12 + (int(in_s) if in_s.strip() else 0)
        return int(s)
    except Exception:
        return 77  # default 6'5"

def height_bucket(h: str) -> str:
    inches = height_to_inches(h)
    if inches <= 76:
        return "short"
    if inches <= 81:
        return "medium"
    return "tall"

def archetype_speed_agility(archetype: str, height: str) -> int:
    bucket = height_bucket(height)
    return HEIGHT_BASED.get(bucket, HEIGHT_BASED["medium"]).get(archetype, 60)

ATTRIBUTES_DEFAULT = [
    ("Finishing", "Close Shot"), ("Finishing", "Driving Layup"),
    ("Finishing", "Driving Dunk"), ("Finishing", "Standing Dunk"),
    ("Finishing", "Post Control"),
    ("Shooting", "Mid-Range Shot"), ("Shooting", "Three-Point Shot"),
    ("Shooting", "Free Throw"),
    ("Playmaking", "Pass Accuracy"), ("Playmaking", "Ball Handle"),
    ("Playmaking", "Speed With Ball"),
    ("Defense", "Interior Defense"), ("Defense", "Perimeter Defense"),
    ("Defense", "Steal"), ("Defense", "Block"),
    ("Rebounding", "Offensive Rebound"), ("Rebounding", "Defensive Rebound"),
    ("Physicals", "Speed"), ("Physicals", "Agility"),
    ("Physicals", "Strength"), ("Physicals", "Vertical"),
]
BADGES_DEFAULT = [
    ("Inside Scoring", "Aerial Wizard"), ("Inside Scoring", "Hook Specialist"),
    ("Inside Scoring", "Layup Mixmaster"), ("Inside Scoring", "Paint Prodigy"),
    ("Inside Scoring", "Physical Finisher"), ("Inside Scoring", "Post Fade Phenom"),
    ("Inside Scoring", "Post Powerhouse"), ("Inside Scoring", "Posterizer"),
    ("Inside Scoring", "Rise Up"),
    ("Outside Scoring", "Deadeye"), ("Outside Scoring", "Limitless Range"),
    ("Outside Scoring", "Mini Marksman"), ("Outside Scoring", "Set Shot Specialist"),
    ("Outside Scoring", "Shifty Shooter"),
    ("Playmaking", "Ankle Assassin"), ("Playmaking", "Bail Out"),
    ("Playmaking", "Break Starter"), ("Playmaking", "Dimer"),
    ("Playmaking", "Handles for Days"), ("Playmaking", "Lightning Launch"),
    ("Playmaking", "Strong Handle"), ("Playmaking", "Unpluckable"),
    ("Playmaking", "Versatile Visionary"),
    ("Defense", "Challenger"), ("Defense", "Glove"),
    ("Defense", "High-Flying Denier"), ("Defense", "Immovable Enforcer"),
    ("Defense", "Interceptor"), ("Defense", "Off-Ball Pest"),
    ("Defense", "On-Ball Menace"), ("Defense", "Paint Patroller"),
    ("Defense", "Pick Dodger"), ("Defense", "Post Lockdown"),
    ("Rebounding", "Boxout Beast"), ("Rebounding", "Rebound Chaser"),
    ("General Offense", "Brick Wall"), ("General Offense", "Slippery Off-Ball"),
    ("General Offense", "All Around"),
    ("Physicals", "Pogo Stick"),
]

# Map attribute name -> category lookup
ATTR_TO_CATEGORY = {name: cat for cat, name in ATTRIBUTES_DEFAULT}

# Universal 2K-style overall formula.
# This intentionally uses ONE formula for every position. A PG, wing, big, or custom build
# is judged by the same attribute priorities instead of position-specific weights.
UNIVERSAL_OVERALL_WEIGHTS = {
    # Highest value: athletic pop and shooting creation/spacing
    "Speed": 2.0,
    "Agility": 2.0,
    "Vertical": 2.0,
    "Mid-Range Shot": 2.0,
    "Three-Point Shot": 2.0,

    # High value: finishing pressure and defensive playmaking
    "Driving Layup": 1.5,
    "Driving Dunk": 1.5,
    "Standing Dunk": 1.5,
    "Steal": 1.5,
    "Block": 1.5,

    # Medium value: playmaking, speed with ball, and rebounding
    "Pass Accuracy": 1.0,
    "Ball Handle": 1.0,
    "Speed With Ball": 1.0,
    "Offensive Rebound": 1.0,
    "Defensive Rebound": 1.0,
}
UNIVERSAL_LOW_VALUE_WEIGHT = 0.6
UNIVERSAL_OVERALL_SCALE_BASE = 20
UNIVERSAL_OVERALL_SCALE_MULTIPLIER = 0.88


def compute_universal_overall(attributes: List[dict]) -> dict:
    """Return one universal overall that values every position the same.

    Priority groups requested for the site:
    - Highest: Speed, Agility, Vertical, Shooting
    - High: Dunks, Layups, Steal, Block
    - Medium: Playmaking, Speed With Ball, Rebounding
    - Low: everything else

    The weighted rating is curved into a more 2K-like 60-99 range so a strong build
    does not look artificially low from low-value attributes dragging down a plain average.
    """
    total = 0.0
    weight_total = 0.0
    groups = {"highest": [], "high": [], "medium": [], "low": []}

    for attr in attributes or []:
        name = attr.get("name", "")
        try:
            rating = int(attr.get("current_level", 50))
        except Exception:
            rating = 50
        rating = max(1, min(99, rating))

        weight = UNIVERSAL_OVERALL_WEIGHTS.get(name, UNIVERSAL_LOW_VALUE_WEIGHT)
        total += rating * weight
        weight_total += weight

        if weight == 2.0:
            groups["highest"].append(name)
        elif weight == 1.5:
            groups["high"].append(name)
        elif weight == 1.0:
            groups["medium"].append(name)
        else:
            groups["low"].append(name)

    weighted_rating = total / weight_total if weight_total else 50.0
    overall = round(UNIVERSAL_OVERALL_SCALE_BASE + (weighted_rating * UNIVERSAL_OVERALL_SCALE_MULTIPLIER))
    overall = max(40, min(99, int(overall)))

    return {
        "overall": overall,
        "weighted_rating": round(weighted_rating, 1),
        "formula": "Universal weighted overall: highest 2.0x, high 1.5x, medium 1.0x, low 0.6x, then curved with 20 + rating * 0.88",
        "weights": {
            "highest": 2.0,
            "high": 1.5,
            "medium": 1.0,
            "low": UNIVERSAL_LOW_VALUE_WEIGHT,
        },
        "groups": groups,
    }

def rating_tier(rating: int) -> str:
    if rating <= 25: return "1-25"
    if rating <= 50: return "26-50"
    if rating <= 75: return "51-75"
    if rating <= 85: return "76-85"
    if rating <= 90: return "86-90"
    return "91-99"

# ===== Password / JWT =====
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False

def get_jwt_secret() -> str:
    return os.environ["JWT_SECRET"]

def create_access_token(user_id: str, email: str) -> str:
    payload = {
        "sub": user_id, "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TTL_MIN),
        "type": "access",
    }
    return jwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)

async def get_current_user(request: Request) -> dict:
    token = None
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
    if not token:
        token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(401, "Not authenticated")
    try:
        payload = jwt.decode(token, get_jwt_secret(), algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "access":
            raise HTTPException(401, "Invalid token type")
        user = await db.users.find_one({"_id": ObjectId(payload["sub"])})
        if not user:
            raise HTTPException(401, "User not found")
        return {"id": str(user["_id"]), "email": user["email"], "name": user.get("name")}
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid token")

# ===== Pydantic models =====
class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)
    name: Optional[str] = None

class LoginIn(BaseModel):
    email: EmailStr
    password: str

class BuildCreate(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    position: str = "PG"
    height: str = "6'5\""

class BuildUpdate(BaseModel):
    name: Optional[str] = None
    position: Optional[str] = None
    height: Optional[str] = None

class GameIn(BaseModel):
    season: int = 1
    date: Optional[str] = None
    event_type: str = "Regular"
    opponent: Optional[str] = ""
    difficulty: str = "Pro"
    result: str = "W"
    minutes: float = 0
    pts: int = 0
    reb: int = 0
    ast: int = 0
    stl: int = 0
    blk: int = 0
    tov: int = 0
    fgm: int = 0
    fga: int = 0
    tpm: int = 0
    tpa: int = 0
    ftm: int = 0
    fta: int = 0
    fouls: int = 0
    player_of_game: bool = False
    notes: Optional[str] = ""

class AttributeUpdate(BaseModel):
    delta: int = 1
    max: bool = False  # buy as many +1 as balance allows

class SettingsPresetIn(BaseModel):
    name: str  # casual | default | hard | hardcore_sim

class ForgotPasswordIn(BaseModel):
    email: EmailStr

class ResetPasswordIn(BaseModel):
    token: str
    password: str = Field(min_length=6)

class BadgeUpdate(BaseModel):
    tier: str

class SettingsUpdate(BaseModel):
    stat_xp: Optional[Dict[str, float]] = None
    bonus_xp: Optional[Dict[str, float]] = None
    attr_costs: Optional[Dict[str, Dict[str, int]]] = None
    badge_tier_cost: Optional[Dict[str, int]] = None

class AwardUpdate(BaseModel):
    season: int
    mvp: Optional[bool] = None
    roy: Optional[bool] = None
    dpoy: Optional[bool] = None
    sixth_man: Optional[bool] = None
    all_nba: Optional[str] = None  # "None" | "1st" | "2nd" | "3rd"

class StartingAttribute(BaseModel):
    name: str
    level: int = Field(ge=25, le=99)

class StartingAttributesIn(BaseModel):
    archetype: Optional[str] = None  # "Sharpshooter" | "Slasher" | "Playmaker" | "Lock" | "Custom"
    attributes: List[StartingAttribute]

# ===== Build helpers =====
def new_build_doc(user_id: str, name: str, position: str, height: str) -> dict:
    attrs = [{"category": cat, "name": n, "starting_level": 50, "current_level": 50, "xp_spent": 0} for cat, n in ATTRIBUTES_DEFAULT]
    badges = [{"category": cat, "name": n, "current_tier": "None", "xp_spent": 0} for cat, n in BADGES_DEFAULT]
    return {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "name": name, "position": position, "height": height,
        "archetype": None,
        "setup_complete": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "attributes": attrs,
        "badges": badges,
        "settings": {
            "stat_xp": dict(DEFAULT_STAT_XP),
            "bonus_xp": dict(DEFAULT_BONUS_XP),
            "attr_costs": default_attr_costs(),
            "badge_tier_cost": dict(DEFAULT_BADGE_TIER_COST),
        },
        "awards": [],  # list of {season, mvp, roy, dpoy, sixth_man, all_nba}
    }

async def get_build_or_404(user_id: str, build_id: str) -> dict:
    b = await db.builds.find_one({"id": build_id, "user_id": user_id}, {"_id": 0})
    if not b:
        raise HTTPException(404, "Build not found")
    return b

def compute_game_xp(g: dict, settings: dict) -> dict:
    stat = settings.get("stat_xp", DEFAULT_STAT_XP)
    bonus = settings.get("bonus_xp", DEFAULT_BONUS_XP)
    if g.get("event_type") == "Offseason":
        return {
            "base_stat_xp": 0, "result_xp": 0, "xp_bonus": int(bonus.get("offseason", 3000)),
            "difficulty_multiplier": 1.0, "final_xp": int(bonus.get("offseason", 3000)),
            "is_double_double": False, "is_triple_double": False, "is_offseason": True,
        }
    base = (
        g.get("pts", 0) * stat.get("pts", 0) +
        g.get("reb", 0) * stat.get("reb", 0) +
        g.get("ast", 0) * stat.get("ast", 0) +
        g.get("stl", 0) * stat.get("stl", 0) +
        g.get("blk", 0) * stat.get("blk", 0) +
        g.get("tov", 0) * stat.get("tov", 0) +
        g.get("fgm", 0) * stat.get("fgm", 0) +
        g.get("tpm", 0) * stat.get("tpm", 0) +
        g.get("ftm", 0) * stat.get("ftm", 0) +
        g.get("fouls", 0) * stat.get("fouls", 0)
    )
    result_xp = bonus.get("win", 0) if g.get("result") == "W" else bonus.get("loss", 0)
    doubles = sum(1 for v in [g.get("pts", 0), g.get("reb", 0), g.get("ast", 0), g.get("stl", 0), g.get("blk", 0)] if v >= 10)
    is_dd = doubles >= 2
    is_td = doubles >= 3
    xp_bonus = 0
    if g.get("player_of_game"):
        xp_bonus += bonus.get("player_of_game", 0)
    if is_td:
        xp_bonus += bonus.get("triple_double", 0)
    elif is_dd:
        xp_bonus += bonus.get("double_double", 0)
    mult = DIFFICULTY_MULTIPLIERS.get(g.get("difficulty", "Pro"), 1.0)
    final = int(round((base + result_xp + xp_bonus) * mult))
    return {
        "base_stat_xp": int(base), "result_xp": int(result_xp), "xp_bonus": int(xp_bonus),
        "difficulty_multiplier": mult, "final_xp": final,
        "is_double_double": is_dd, "is_triple_double": is_td, "is_offseason": False,
    }

async def build_state(user_id: str, build_id: str) -> dict:
    b = await get_build_or_404(user_id, build_id)
    games = await db.games.find({"build_id": build_id, "user_id": user_id}, {"_id": 0}).sort("game_num", 1).to_list(10000)
    earned = sum(g.get("final_xp", 0) for g in games)
    a_spent = sum(a.get("xp_spent", 0) for a in b.get("attributes", []))
    g_spent = sum(x.get("xp_spent", 0) for x in b.get("badges", []))
    overall_info = compute_universal_overall(b.get("attributes", []))
    return {
        "build": b,
        "games": games,
        "summary": {
            "xp_earned": earned,
            "attribute_xp_spent": a_spent,
            "badge_xp_spent": g_spent,
            "shared_balance": earned - a_spent - g_spent,
            "overall": overall_info["overall"],
            "overall_breakdown": overall_info,
        },
        "constants": {
            "difficulties": list(DIFFICULTY_MULTIPLIERS.keys()),
            "difficulty_multipliers": DIFFICULTY_MULTIPLIERS,
            "badge_tier_order": BADGE_TIER_ORDER,
            "attribute_categories": ATTRIBUTE_CATEGORIES,
            "rating_tiers": RATING_TIERS,
        },
    }

# ===== Routes =====
app = FastAPI()
api_router = APIRouter(prefix="/api")

@api_router.get("/")
async def root():
    return {"message": "2K Career Engine API"}

# --- Auth ---
@api_router.post("/auth/register")
async def register(body: RegisterIn):
    email = body.email.lower()
    existing = await db.users.find_one({"email": email})
    if existing:
        raise HTTPException(400, "Email already registered")
    doc = {
        "email": email,
        "password_hash": hash_password(body.password),
        "name": body.name or email.split("@")[0],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    res = await db.users.insert_one(doc)
    uid = str(res.inserted_id)
    token = create_access_token(uid, email)
    return {"token": token, "user": {"id": uid, "email": email, "name": doc["name"]}}

@api_router.post("/auth/login")
async def login(body: LoginIn):
    email = body.email.lower()
    user = await db.users.find_one({"email": email})
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(401, "Invalid credentials")
    uid = str(user["_id"])
    token = create_access_token(uid, email)
    return {"token": token, "user": {"id": uid, "email": email, "name": user.get("name")}}

@api_router.get("/auth/me")
async def me(user: dict = Depends(get_current_user)):
    return user

# --- Builds ---
@api_router.get("/builds")
async def list_builds(user: dict = Depends(get_current_user)):
    builds = await db.builds.find({"user_id": user["id"]}, {"_id": 0}).sort("created_at", 1).to_list(1000)
    # add quick summary stats
    out = []
    for b in builds:
        games_count = await db.games.count_documents({"build_id": b["id"], "user_id": user["id"]})
        earned = 0
        async for g in db.games.find({"build_id": b["id"], "user_id": user["id"]}, {"_id": 0, "final_xp": 1}):
            earned += g.get("final_xp", 0)
        a_spent = sum(a.get("xp_spent", 0) for a in b.get("attributes", []))
        bd_spent = sum(x.get("xp_spent", 0) for x in b.get("badges", []))
        ovr = compute_universal_overall(b.get("attributes", [])).get("overall", 60)
        out.append({
            "id": b["id"], "name": b["name"], "position": b.get("position"), "height": b.get("height"),
            "created_at": b.get("created_at"),
            "games_count": games_count,
            "overall": ovr,
            "xp_earned": earned,
            "balance": earned - a_spent - bd_spent,
        })
    return out

@api_router.post("/builds")
async def create_build(body: BuildCreate, user: dict = Depends(get_current_user)):
    doc = new_build_doc(user["id"], body.name, body.position, body.height)
    await db.builds.insert_one(doc)
    doc.pop("_id", None)
    return doc

@api_router.get("/builds/{build_id}")
async def get_build_state(build_id: str, user: dict = Depends(get_current_user)):
    return await build_state(user["id"], build_id)

@api_router.put("/builds/{build_id}")
async def update_build(build_id: str, body: BuildUpdate, user: dict = Depends(get_current_user)):
    await get_build_or_404(user["id"], build_id)
    update = {k: v for k, v in body.model_dump().items() if v is not None}
    if update:
        await db.builds.update_one({"id": build_id, "user_id": user["id"]}, {"$set": update})
    return await get_build_or_404(user["id"], build_id)

@api_router.delete("/builds/{build_id}")
async def delete_build(build_id: str, user: dict = Depends(get_current_user)):
    await get_build_or_404(user["id"], build_id)
    await db.builds.delete_one({"id": build_id, "user_id": user["id"]})
    await db.games.delete_many({"build_id": build_id, "user_id": user["id"]})
    return {"ok": True}

@api_router.post("/builds/{build_id}/reset")
async def reset_build(build_id: str, user: dict = Depends(get_current_user)):
    b = await get_build_or_404(user["id"], build_id)
    fresh = new_build_doc(user["id"], b["name"], b.get("position", "PG"), b.get("height", ""))
    fresh["id"] = build_id  # keep id
    fresh["created_at"] = b.get("created_at")
    # keep settings if user customized
    fresh["settings"] = b.get("settings", fresh["settings"])
    await db.builds.replace_one({"id": build_id, "user_id": user["id"]}, fresh)
    await db.games.delete_many({"build_id": build_id, "user_id": user["id"]})
    return {"ok": True}

# --- Games ---
@api_router.post("/builds/{build_id}/games/preview")
async def preview_game(build_id: str, g: GameIn, user: dict = Depends(get_current_user)):
    b = await get_build_or_404(user["id"], build_id)
    return compute_game_xp(g.model_dump(), b.get("settings", {}))

@api_router.post("/builds/{build_id}/games")
async def add_game(build_id: str, g: GameIn, user: dict = Depends(get_current_user)):
    b = await get_build_or_404(user["id"], build_id)
    data = g.model_dump()
    if not data.get("date"):
        data["date"] = datetime.now(timezone.utc).date().isoformat()
    xp_info = compute_game_xp(data, b.get("settings", {}))
    count = await db.games.count_documents({"build_id": build_id, "user_id": user["id"]})
    doc = {
        "id": str(uuid.uuid4()),
        "build_id": build_id,
        "user_id": user["id"],
        "game_num": count + 1,
        **data,
        **xp_info,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.games.insert_one(doc)
    doc.pop("_id", None)
    return doc

@api_router.delete("/builds/{build_id}/games/{game_id}")
async def delete_game(build_id: str, game_id: str, user: dict = Depends(get_current_user)):
    await get_build_or_404(user["id"], build_id)
    res = await db.games.delete_one({"id": game_id, "build_id": build_id, "user_id": user["id"]})
    if res.deleted_count == 0:
        raise HTTPException(404, "Not found")
    games = await db.games.find({"build_id": build_id, "user_id": user["id"]}, {"_id": 0}).sort("created_at", 1).to_list(10000)
    for i, gm in enumerate(games):
        await db.games.update_one({"id": gm["id"]}, {"$set": {"game_num": i + 1}})
    return {"ok": True}

# --- Attributes ---
def attr_cost_for(build: dict, category: str, current_level: int) -> int:
    tier = rating_tier(current_level)
    costs = build.get("settings", {}).get("attr_costs", default_attr_costs())
    cat_costs = costs.get(category, costs.get(ATTRIBUTE_CATEGORIES[0]))
    return int(cat_costs.get(tier, 0))

@api_router.put("/builds/{build_id}/attributes/{name}")
async def update_attribute(build_id: str, name: str, body: AttributeUpdate, user: dict = Depends(get_current_user)):
    b = await get_build_or_404(user["id"], build_id)
    state = await build_state(user["id"], build_id)
    balance = state["summary"]["shared_balance"]

    attrs = b["attributes"]
    idx = next((i for i, a in enumerate(attrs) if a["name"] == name), -1)
    if idx < 0:
        raise HTTPException(404, "attribute not found")
    a = attrs[idx]
    cat = a["category"]

    if body.max or body.delta > 0:
        target = 999 if body.max else int(body.delta)
        bought = 0
        spent = 0
        remaining = balance
        cur_lvl = a["current_level"]
        while bought < target and cur_lvl < 99:
            cost = attr_cost_for(b, cat, cur_lvl)
            if cost > remaining:
                break
            remaining -= cost
            spent += cost
            cur_lvl += 1
            bought += 1
        if bought == 0:
            cost = attr_cost_for(b, cat, a["current_level"])
            raise HTTPException(400, f"Not enough XP. Need {cost}, have {balance}")
        a["current_level"] = cur_lvl
        a["xp_spent"] += spent
    elif body.delta < 0:
        refunded = 0
        for _ in range(abs(int(body.delta))):
            if a["current_level"] <= a["starting_level"]:
                break
            refund = attr_cost_for(b, cat, a["current_level"] - 1)
            a["current_level"] -= 1
            a["xp_spent"] = max(0, a["xp_spent"] - refund)
            refunded += 1
        if refunded == 0:
            raise HTTPException(400, "Cannot downgrade below starting level")
    else:
        raise HTTPException(400, "delta must not be 0")
    attrs[idx] = a
    await db.builds.update_one({"id": build_id, "user_id": user["id"]}, {"$set": {"attributes": attrs}})
    return a

# --- Badges ---
@api_router.put("/builds/{build_id}/badges/{name}")
async def update_badge(build_id: str, name: str, body: BadgeUpdate, user: dict = Depends(get_current_user)):
    if body.tier not in BADGE_TIER_ORDER:
        raise HTTPException(400, "invalid tier")
    b = await get_build_or_404(user["id"], build_id)
    state = await build_state(user["id"], build_id)
    balance = state["summary"]["shared_balance"]

    badges = b["badges"]
    idx = next((i for i, x in enumerate(badges) if x["name"] == name), -1)
    if idx < 0:
        raise HTTPException(404, "badge not found")
    bd = badges[idx]
    new_idx = BADGE_TIER_ORDER.index(body.tier)
    costs = b.get("settings", {}).get("badge_tier_cost", DEFAULT_BADGE_TIER_COST)
    def cumulative(i_):
        return sum(int(costs.get(BADGE_TIER_ORDER[i], 0)) for i in range(i_ + 1))
    target_cost = cumulative(new_idx)
    delta_cost = target_cost - bd["xp_spent"]
    if delta_cost > balance:
        raise HTTPException(400, f"Not enough XP. Need {delta_cost} more, have {balance}")
    bd["current_tier"] = body.tier
    bd["xp_spent"] = target_cost
    badges[idx] = bd
    await db.builds.update_one({"id": build_id, "user_id": user["id"]}, {"$set": {"badges": badges}})
    return bd

# --- Settings ---
@api_router.put("/builds/{build_id}/settings")
async def update_settings(build_id: str, body: SettingsUpdate, user: dict = Depends(get_current_user)):
    b = await get_build_or_404(user["id"], build_id)
    settings = b.get("settings", {})
    if body.stat_xp is not None:
        settings["stat_xp"] = {**settings.get("stat_xp", DEFAULT_STAT_XP), **body.stat_xp}
    if body.bonus_xp is not None:
        settings["bonus_xp"] = {**settings.get("bonus_xp", DEFAULT_BONUS_XP), **body.bonus_xp}
    if body.attr_costs is not None:
        cur = settings.get("attr_costs", default_attr_costs())
        for cat, tier_map in body.attr_costs.items():
            cur[cat] = {**cur.get(cat, {}), **{k: int(v) for k, v in tier_map.items()}}
        settings["attr_costs"] = cur
    if body.badge_tier_cost is not None:
        settings["badge_tier_cost"] = {**settings.get("badge_tier_cost", DEFAULT_BADGE_TIER_COST), **{k: int(v) for k, v in body.badge_tier_cost.items()}}
    await db.builds.update_one({"id": build_id, "user_id": user["id"]}, {"$set": {"settings": settings}})
    return settings

@api_router.post("/builds/{build_id}/settings/reset")
async def reset_settings(build_id: str, user: dict = Depends(get_current_user)):
    await get_build_or_404(user["id"], build_id)
    settings = {
        "stat_xp": dict(DEFAULT_STAT_XP),
        "bonus_xp": dict(DEFAULT_BONUS_XP),
        "attr_costs": default_attr_costs(),
        "badge_tier_cost": dict(DEFAULT_BADGE_TIER_COST),
    }
    await db.builds.update_one({"id": build_id, "user_id": user["id"]}, {"$set": {"settings": settings}})
    return settings

def _scale_attr_costs(scale: float) -> dict:
    return {cat: {tier: max(1, int(round(v * scale))) for tier, v in tiers.items()}
            for cat, tiers in DEFAULT_ATTR_COSTS.items()}

def _scale_dict(d, scale, floor=None):
    out = {}
    for k, v in d.items():
        if isinstance(v, (int, float)):
            val = int(round(v * scale))
            if floor is not None and val < floor:
                val = floor
            out[k] = val
        else:
            out[k] = v
    return out

SETTINGS_PRESETS = {
    "casual": {
        "stat_xp": _scale_dict(DEFAULT_STAT_XP, 1.5),
        "bonus_xp": _scale_dict(DEFAULT_BONUS_XP, 2.0),
        "attr_costs": _scale_attr_costs(0.5),
        "badge_tier_cost": _scale_dict(DEFAULT_BADGE_TIER_COST, 0.5),
    },
    "default": {
        "stat_xp": dict(DEFAULT_STAT_XP),
        "bonus_xp": dict(DEFAULT_BONUS_XP),
        "attr_costs": default_attr_costs(),
        "badge_tier_cost": dict(DEFAULT_BADGE_TIER_COST),
    },
    "hard": {
        "stat_xp": _scale_dict(DEFAULT_STAT_XP, 0.75),
        "bonus_xp": _scale_dict(DEFAULT_BONUS_XP, 0.6),
        "attr_costs": _scale_attr_costs(1.5),
        "badge_tier_cost": _scale_dict(DEFAULT_BADGE_TIER_COST, 1.5),
    },
    "hardcore_sim": {
        "stat_xp": _scale_dict(DEFAULT_STAT_XP, 0.5),
        "bonus_xp": _scale_dict(DEFAULT_BONUS_XP, 0.3),
        "attr_costs": _scale_attr_costs(2.0),
        "badge_tier_cost": _scale_dict(DEFAULT_BADGE_TIER_COST, 2.0),
    },
}

@api_router.post("/builds/{build_id}/settings/preset")
async def apply_settings_preset(build_id: str, body: SettingsPresetIn, user: dict = Depends(get_current_user)):
    await get_build_or_404(user["id"], build_id)
    preset = SETTINGS_PRESETS.get(body.name)
    if not preset:
        raise HTTPException(400, f"Unknown preset '{body.name}'")
    # deep-copy so mutating doesn't affect constants
    settings = {
        "stat_xp": dict(preset["stat_xp"]),
        "bonus_xp": dict(preset["bonus_xp"]),
        "attr_costs": {cat: dict(tiers) for cat, tiers in preset["attr_costs"].items()},
        "badge_tier_cost": dict(preset["badge_tier_cost"]),
    }
    await db.builds.update_one({"id": build_id, "user_id": user["id"]}, {"$set": {"settings": settings}})
    return {"preset": body.name, "settings": settings}

@api_router.get("/settings/presets")
async def list_presets():
    return {"presets": list(SETTINGS_PRESETS.keys())}

# --- Archetypes ---
@api_router.get("/archetypes")
async def get_archetypes():
    """Returns archetype definitions for the Build Setup screen."""
    full = {}
    for name, attrs in ARCHETYPES.items():
        # add per-bucket Speed/Agility for preview
        full[name] = {
            **attrs,
            "_height_based": {
                bucket: {"Speed": HEIGHT_BASED[bucket][name], "Agility": HEIGHT_BASED[bucket][name]}
                for bucket in HEIGHT_BASED
            },
        }
    return {
        "archetypes": full,
        "buckets": HEIGHT_BUCKET_LABELS,
        "height_based_attrs": ["Speed", "Agility"],
    }

@api_router.put("/builds/{build_id}/starting-attributes")
async def set_starting_attributes(build_id: str, body: StartingAttributesIn, user: dict = Depends(get_current_user)):
    b = await get_build_or_404(user["id"], build_id)
    # Apply provided levels
    levels = {item.name: int(item.level) for item in body.attributes}
    attrs = b["attributes"]
    for i, a in enumerate(attrs):
        if a["name"] in levels:
            lv = max(25, min(99, levels[a["name"]]))
            a["starting_level"] = lv
            a["current_level"] = lv
            a["xp_spent"] = 0
        attrs[i] = a
    update = {"attributes": attrs, "setup_complete": True}
    if body.archetype:
        update["archetype"] = body.archetype
    await db.builds.update_one({"id": build_id, "user_id": user["id"]}, {"$set": update})
    return await get_build_or_404(user["id"], build_id)

# --- Password Reset ---
import secrets as _secrets

@api_router.post("/auth/forgot-password")
async def forgot_password(body: ForgotPasswordIn):
    email = body.email.lower()
    user = await db.users.find_one({"email": email})
    # Always succeed silently for non-existing emails
    if not user:
        return {"ok": True, "reset_url": None, "info": "If that email exists, a reset link has been generated."}
    token = _secrets.token_urlsafe(32)
    await db.password_reset_tokens.insert_one({
        "token": token,
        "user_id": str(user["_id"]),
        "email": email,
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
        "used": False,
        "created_at": datetime.now(timezone.utc),
    })
    reset_path = f"/reset-password?token={token}"
    logger.info(f"[PASSWORD RESET] {email} -> {reset_path}")
    # No email service configured; return URL so user can use it directly.
    return {"ok": True, "reset_url": reset_path}

@api_router.post("/auth/reset-password")
async def reset_password(body: ResetPasswordIn):
    rec = await db.password_reset_tokens.find_one({"token": body.token})
    if not rec:
        raise HTTPException(400, "Invalid or expired token")
    if rec.get("used"):
        raise HTTPException(400, "Token already used")
    expires = rec.get("expires_at")
    if isinstance(expires, datetime):
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires < datetime.now(timezone.utc):
            raise HTTPException(400, "Token expired")
    new_hash = hash_password(body.password)
    await db.users.update_one({"_id": ObjectId(rec["user_id"])}, {"$set": {"password_hash": new_hash}})
    await db.password_reset_tokens.update_one({"token": body.token}, {"$set": {"used": True, "used_at": datetime.now(timezone.utc)}})
    return {"ok": True}

# --- Awards ---
@api_router.put("/builds/{build_id}/awards")
async def update_award(build_id: str, body: AwardUpdate, user: dict = Depends(get_current_user)):
    b = await get_build_or_404(user["id"], build_id)
    awards = b.get("awards", [])
    idx = next((i for i, a in enumerate(awards) if a["season"] == body.season), -1)
    if idx < 0:
        awards.append({"season": body.season, "mvp": False, "roy": False, "dpoy": False, "sixth_man": False, "all_nba": "None"})
        idx = len(awards) - 1
    aw = awards[idx]
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        if k == "season" or v is None:
            continue
        aw[k] = v
    awards[idx] = aw
    await db.builds.update_one({"id": build_id, "user_id": user["id"]}, {"$set": {"awards": awards}})
    return awards

# ===== Startup =====
async def ensure_indexes():
    await db.users.create_index("email", unique=True)
    await db.builds.create_index([("user_id", 1), ("id", 1)])
    await db.games.create_index([("build_id", 1), ("user_id", 1)])
    await db.password_reset_tokens.create_index("token", unique=True)
    await db.password_reset_tokens.create_index("expires_at", expireAfterSeconds=0)

async def seed_admin():
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@example.com").lower()
    admin_password = os.environ.get("ADMIN_PASSWORD", "admin123")
    existing = await db.users.find_one({"email": admin_email})
    if existing is None:
        await db.users.insert_one({
            "email": admin_email, "password_hash": hash_password(admin_password),
            "name": "Admin", "role": "admin",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    elif not verify_password(admin_password, existing["password_hash"]):
        await db.users.update_one({"email": admin_email}, {"$set": {"password_hash": hash_password(admin_password)}})

@app.on_event("startup")
async def on_startup():
    await ensure_indexes()
    await seed_admin()

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()

app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

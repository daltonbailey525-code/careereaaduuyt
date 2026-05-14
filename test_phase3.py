"""Phase 3 tests: password reset, settings presets, bulk attribute upgrades."""
import os
import uuid
import time
import pytest
import requests
from datetime import datetime, timezone, timedelta
from pymongo import MongoClient

BASE_URL = os.environ['REACT_APP_BACKEND_URL'].rstrip('/')
API = f"{BASE_URL}/api"

MONGO_URL = os.environ.get('MONGO_URL', 'mongodb://localhost:27017')
DB_NAME = os.environ.get('DB_NAME', 'test_database')


def _email():
    return f"TEST_{uuid.uuid4().hex[:10]}@example.com"


def _register(email=None, password="test1234"):
    email = email or _email()
    r = requests.post(f"{API}/auth/register", json={"email": email, "password": password})
    r.raise_for_status()
    return email, password, r.json()["token"]


def _hdr(t):
    return {"Authorization": f"Bearer {t}"}


# ===== Password Reset =====
class TestPasswordReset:
    def test_forgot_existing_user_returns_url(self):
        email, pw, _ = _register()
        r = requests.post(f"{API}/auth/forgot-password", json={"email": email})
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert d["reset_url"] and d["reset_url"].startswith("/reset-password?token=")

    def test_forgot_unknown_email_no_enumeration(self):
        r = requests.post(f"{API}/auth/forgot-password", json={"email": f"nope_{uuid.uuid4().hex[:6]}@x.com"})
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert d.get("reset_url") is None

    def test_reset_with_valid_token_and_login(self):
        email, _, _ = _register(password="oldpass1")
        r = requests.post(f"{API}/auth/forgot-password", json={"email": email}).json()
        token = r["reset_url"].split("token=")[1]
        rr = requests.post(f"{API}/auth/reset-password", json={"token": token, "password": "newpass1"})
        assert rr.status_code == 200
        # old password fails
        bad = requests.post(f"{API}/auth/login", json={"email": email, "password": "oldpass1"})
        assert bad.status_code == 401
        # new password works
        ok = requests.post(f"{API}/auth/login", json={"email": email, "password": "newpass1"})
        assert ok.status_code == 200

    def test_reset_invalid_token_400(self):
        r = requests.post(f"{API}/auth/reset-password", json={"token": "nonexistent_xyz", "password": "abcdef"})
        assert r.status_code == 400

    def test_reset_reused_token_400(self):
        email, _, _ = _register()
        token = requests.post(f"{API}/auth/forgot-password", json={"email": email}).json()["reset_url"].split("token=")[1]
        r1 = requests.post(f"{API}/auth/reset-password", json={"token": token, "password": "newpass1"})
        assert r1.status_code == 200
        r2 = requests.post(f"{API}/auth/reset-password", json={"token": token, "password": "newpass2"})
        assert r2.status_code == 400
        assert "used" in r2.json().get("detail", "").lower()

    def test_reset_expired_token_400(self):
        email, _, _ = _register()
        token = requests.post(f"{API}/auth/forgot-password", json={"email": email}).json()["reset_url"].split("token=")[1]
        # Manually expire token via direct DB write
        mc = MongoClient(MONGO_URL)
        db = mc[DB_NAME]
        res = db.password_reset_tokens.update_one(
            {"token": token},
            {"$set": {"expires_at": datetime.now(timezone.utc) - timedelta(hours=2)}},
        )
        assert res.modified_count == 1
        r = requests.post(f"{API}/auth/reset-password", json={"token": token, "password": "newpass1"})
        assert r.status_code == 400
        assert "expired" in r.json().get("detail", "").lower()


# ===== Settings Presets =====
class TestSettingsPresets:
    def test_list_presets(self):
        r = requests.get(f"{API}/settings/presets")
        assert r.status_code == 200
        assert set(r.json()["presets"]) == {"casual", "default", "hard", "hardcore_sim"}

    def _bid(self):
        _, _, t = _register()
        b = requests.post(f"{API}/builds", json={"name": "P3", "position": "PG", "height": "6'5\""}, headers=_hdr(t)).json()
        return b["id"], t

    def test_apply_hardcore_sim(self):
        bid, t = self._bid()
        r = requests.post(f"{API}/builds/{bid}/settings/preset", json={"name": "hardcore_sim"}, headers=_hdr(t))
        assert r.status_code == 200
        s = r.json()["settings"]
        assert s["stat_xp"]["pts"] == 5
        assert s["bonus_xp"]["win"] == 200
        assert s["attr_costs"]["Finishing"]["1-25"] == 50
        assert s["badge_tier_cost"]["Bronze"] == 100

    def test_apply_casual(self):
        bid, t = self._bid()
        r = requests.post(f"{API}/builds/{bid}/settings/preset", json={"name": "casual"}, headers=_hdr(t))
        assert r.status_code == 200
        s = r.json()["settings"]
        assert s["stat_xp"]["pts"] == 20
        assert s["badge_tier_cost"]["Bronze"] == 25

    def test_apply_default_restores(self):
        bid, t = self._bid()
        requests.post(f"{API}/builds/{bid}/settings/preset", json={"name": "casual"}, headers=_hdr(t))
        r = requests.post(f"{API}/builds/{bid}/settings/preset", json={"name": "default"}, headers=_hdr(t))
        s = r.json()["settings"]
        assert s["stat_xp"]["pts"] == 10
        assert s["badge_tier_cost"]["Bronze"] == 50

    def test_unknown_preset_400(self):
        bid, t = self._bid()
        r = requests.post(f"{API}/builds/{bid}/settings/preset", json={"name": "ultra"}, headers=_hdr(t))
        assert r.status_code == 400

    def test_preset_requires_auth(self):
        bid, _ = self._bid()
        r = requests.post(f"{API}/builds/{bid}/settings/preset", json={"name": "casual"})
        assert r.status_code == 401


# ===== Bulk Attribute Upgrade =====
def _setup_build_with_xp(target_xp):
    """Register user, create build, add games to earn ~target_xp using a high-stat preset."""
    _, _, t = _register()
    b = requests.post(f"{API}/builds", json={"name": "X", "position": "PG", "height": "6'5\""}, headers=_hdr(t)).json()
    bid = b["id"]
    # Apply casual to make XP earning easy
    requests.post(f"{API}/builds/{bid}/settings/preset", json={"name": "casual"}, headers=_hdr(t))
    # Each casual game: pts 50 * 20 = 1000 + win 1000 = 2000 XP
    games_needed = max(1, target_xp // 2000 + 1)
    for _ in range(games_needed):
        requests.post(f"{API}/builds/{bid}/games", json={
            "season": 1, "event_type": "Regular", "difficulty": "Pro", "result": "W",
            "pts": 50, "reb": 0, "ast": 0, "stl": 0, "blk": 0, "tov": 0, "fgm": 0, "tpm": 0, "ftm": 0, "fouls": 0,
        }, headers=_hdr(t))
    # Reset settings back to default so attr_costs are predictable
    requests.post(f"{API}/builds/{bid}/settings/preset", json={"name": "default"}, headers=_hdr(t))
    return bid, t


class TestBulkAttribute:
    def test_delta_5_sufficient_xp(self):
        bid, t = _setup_build_with_xp(1000)
        # Starting lvl 50 (tier 26-50=50). lvl50→51 pays 50. lvl51..55 are tier 51-75 cost 75 each = 4*75=300. Total 350.
        attr = "Close Shot"
        r = requests.put(f"{API}/builds/{bid}/attributes/{attr}", json={"delta": 5}, headers=_hdr(t))
        assert r.status_code == 200
        a = r.json()
        assert a["current_level"] == 55
        assert a["xp_spent"] == 350

    def test_delta_5_partial_buy(self):
        # Need precisely 75 XP balance, default tier 26-50 cost=50; one buy at 50 then next at 75 (tier 51-75 = 75) cost = 125 total for 2, can't afford 2.
        # Actually default cost tier 26-50 = 50; level 50→51 costs 50, then level 51 enters tier 51-75 cost=75. So with 75 XP balance we buy 1 (cost 50), then 51 needs 75 but 25 left -> stop. Got 1.
        # To verify partial: register fresh, add 1 game default settings to earn small XP.
        _, _, t = _register()
        b = requests.post(f"{API}/builds", json={"name": "Z", "position": "PG", "height": "6'5\""}, headers=_hdr(t)).json()
        bid = b["id"]
        # default stat_xp pts=10. We want ~75 XP balance: 1 game with pts=0, fgm=0, result=L (0 bonus), so 0 xp. Then we need exactly 75.
        # Use a game pts=7, ftm=0 with stat_xp default pts=10 → 70 XP, result=L → 0 bonus. = 70 close to 75. Use pts=8 -> 80.
        requests.post(f"{API}/builds/{bid}/games", json={
            "season": 1, "event_type": "Regular", "difficulty": "Pro", "result": "L",
            "pts": 8, "reb": 0, "ast": 0, "stl": 0, "blk": 0, "tov": 0, "fgm": 0, "tpm": 0, "ftm": 0, "fouls": 0,
        }, headers=_hdr(t))
        # balance = 80. tier 26-50=50. buys lvl50→51 (cost 50). 30 left, tier 51-75 cost 75. Stop. delta=5 partial => 1 bought.
        r = requests.put(f"{API}/builds/{bid}/attributes/Close Shot", json={"delta": 5}, headers=_hdr(t))
        assert r.status_code == 200
        a = r.json()
        assert a["current_level"] == 51
        assert a["xp_spent"] == 50

    def test_delta_insufficient_for_any_400(self):
        _, _, t = _register()
        b = requests.post(f"{API}/builds", json={"name": "Z", "position": "PG", "height": "6'5\""}, headers=_hdr(t)).json()
        bid = b["id"]
        r = requests.put(f"{API}/builds/{bid}/attributes/Close Shot", json={"delta": 5}, headers=_hdr(t))
        assert r.status_code == 400

    def test_max_caps_at_99(self):
        bid, t = _setup_build_with_xp(200000)  # huge balance
        r = requests.put(f"{API}/builds/{bid}/attributes/Close Shot", json={"delta": 1, "max": True}, headers=_hdr(t))
        assert r.status_code == 200
        a = r.json()
        assert a["current_level"] == 99
        # 50→51 (tier 26-50=50) + lvl51..75 (25 buys * 75) + lvl76..85 (10*100) + lvl86..90 (5*125) + lvl91..98 (8*225)
        # = 50 + 1875 + 1000 + 625 + 1800 = 5350
        assert a["xp_spent"] == 5350

    def test_refund_stops_at_starting(self):
        bid, t = _setup_build_with_xp(1000)
        # buy +5 first then refund -10 -> should stop at starting_level=50
        requests.put(f"{API}/builds/{bid}/attributes/Close Shot", json={"delta": 3}, headers=_hdr(t))
        r = requests.put(f"{API}/builds/{bid}/attributes/Close Shot", json={"delta": -10}, headers=_hdr(t))
        assert r.status_code == 200
        a = r.json()
        assert a["current_level"] == 50  # capped to starting
        assert a["xp_spent"] == 0  # fully refunded

    def test_no_auth_401(self):
        _, _, t = _register()
        b = requests.post(f"{API}/builds", json={"name": "Z", "position": "PG", "height": "6'5\""}, headers=_hdr(t)).json()
        r = requests.put(f"{API}/builds/{b['id']}/attributes/Close Shot", json={"delta": 1})
        assert r.status_code == 401


# ===== Regression =====
class TestRegression:
    def test_auth_me_still_works(self):
        _, _, t = _register()
        r = requests.get(f"{API}/auth/me", headers=_hdr(t))
        assert r.status_code == 200

    def test_build_isolation(self):
        _, _, ta = _register()
        _, _, tb = _register()
        b = requests.post(f"{API}/builds", json={"name": "A", "position": "PG", "height": "6'5\""}, headers=_hdr(ta)).json()
        r = requests.get(f"{API}/builds/{b['id']}", headers=_hdr(tb))
        assert r.status_code == 404

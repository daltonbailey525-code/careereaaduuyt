"""Phase 2 backend API tests for 2K Career Engine.

Covers JWT auth, multi-build isolation, per-build settings (stat_xp/bonus_xp/
attr_costs with 6 categories × 6 tiers, badge_tier_cost), preview/games using
build settings, attribute & badge cost lookup via build settings, awards merge.
"""
import os
import uuid
import pytest
import requests
from dotenv import load_dotenv
load_dotenv("/app/frontend/.env")

BASE_URL = os.environ['REACT_APP_BACKEND_URL'].rstrip('/')
API = f"{BASE_URL}/api"

ADMIN_EMAIL = "admin@2kengine.app"
ADMIN_PASSWORD = "admin123"


def _unique_email(prefix="TEST"):
    return f"{prefix}_{uuid.uuid4().hex[:10]}@example.com"


def _register(email=None, password="test1234", name=None):
    email = email or _unique_email()
    r = requests.post(f"{API}/auth/register",
                      json={"email": email, "password": password, "name": name or "Tester"},
                      timeout=20)
    return r, email, password


def _auth_headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# ------------------------------------------------------------------
# Auth
# ------------------------------------------------------------------
class TestAuth:
    def test_register_returns_token_and_user(self):
        r, email, _ = _register()
        assert r.status_code == 200, r.text
        d = r.json()
        assert "token" in d and isinstance(d["token"], str) and len(d["token"]) > 20
        assert d["user"]["email"] == email.lower()
        assert "id" in d["user"]

    def test_register_duplicate_email_400(self):
        _, email, pw = _register()
        r, _, _ = _register(email=email, password=pw)
        assert r.status_code == 400

    def test_login_success_and_me(self):
        _, email, pw = _register()
        r = requests.post(f"{API}/auth/login", json={"email": email, "password": pw}, timeout=20)
        assert r.status_code == 200
        token = r.json()["token"]
        # me
        rm = requests.get(f"{API}/auth/me", headers=_auth_headers(token), timeout=20)
        assert rm.status_code == 200
        assert rm.json()["email"] == email.lower()

    def test_login_wrong_password_401(self):
        _, email, _ = _register()
        r = requests.post(f"{API}/auth/login", json={"email": email, "password": "wrongpass"}, timeout=20)
        assert r.status_code == 401

    def test_protected_no_token_401(self):
        r = requests.get(f"{API}/builds", timeout=20)
        assert r.status_code == 401

    def test_admin_seeded_login(self):
        r = requests.post(f"{API}/auth/login",
                          json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=20)
        assert r.status_code == 200, r.text


# ------------------------------------------------------------------
# Builds CRUD + isolation
# ------------------------------------------------------------------
@pytest.fixture(scope="module")
def user_a():
    r, email, pw = _register()
    return {"token": r.json()["token"], "email": email}


@pytest.fixture(scope="module")
def user_b():
    r, email, pw = _register()
    return {"token": r.json()["token"], "email": email}


class TestBuilds:
    def test_create_build_defaults(self, user_a):
        r = requests.post(f"{API}/builds", headers=_auth_headers(user_a["token"]),
                          json={"name": "TEST_Build1", "position": "PG", "height": "6'5\""})
        assert r.status_code == 200, r.text
        b = r.json()
        assert b["name"] == "TEST_Build1"
        assert b["position"] == "PG"
        assert "id" in b
        # defaults
        s = b["settings"]
        assert s["stat_xp"]["pts"] == 10
        assert s["stat_xp"]["fouls"] == -10
        assert s["bonus_xp"]["offseason"] == 3000
        # 6 categories x 6 tiers
        assert set(s["attr_costs"].keys()) == {"Finishing","Shooting","Playmaking","Defense","Rebounding","Physicals"}
        tiers = ["1-25","26-50","51-75","76-85","86-90","91-99"]
        for cat, tier_map in s["attr_costs"].items():
            assert list(tier_map.keys()) == tiers
            assert [tier_map[t] for t in tiers] == [25,50,75,100,125,225]
        assert s["badge_tier_cost"]["Bronze"] == 50
        assert s["badge_tier_cost"]["HOF"] == 225
        user_a["build_id"] = b["id"]

    def test_list_builds_summary_fields_isolated(self, user_a, user_b):
        # create one for B too
        rb = requests.post(f"{API}/builds", headers=_auth_headers(user_b["token"]),
                           json={"name": "TEST_B_Build", "position": "SG", "height": "6'6\""})
        assert rb.status_code == 200
        user_b["build_id"] = rb.json()["id"]
        # A sees only A's builds
        la = requests.get(f"{API}/builds", headers=_auth_headers(user_a["token"])).json()
        ids_a = {b["id"] for b in la}
        assert user_a["build_id"] in ids_a
        assert user_b["build_id"] not in ids_a
        # required summary fields
        first = la[0]
        for k in ["id","name","position","height","games_count","overall","xp_earned","balance"]:
            assert k in first, f"missing {k}"

    def test_other_users_build_returns_404(self, user_a, user_b):
        r = requests.get(f"{API}/builds/{user_b['build_id']}", headers=_auth_headers(user_a["token"]))
        assert r.status_code == 404

    def test_delete_build_removes_games(self, user_a):
        # create disposable build
        r = requests.post(f"{API}/builds", headers=_auth_headers(user_a["token"]),
                          json={"name":"TEST_Dispose","position":"PG","height":"6'5\""})
        bid = r.json()["id"]
        # add game
        rg = requests.post(f"{API}/builds/{bid}/games", headers=_auth_headers(user_a["token"]),
                           json={"event_type":"Regular","difficulty":"Pro","result":"W","pts":10})
        assert rg.status_code == 200
        rd = requests.delete(f"{API}/builds/{bid}", headers=_auth_headers(user_a["token"]))
        assert rd.status_code == 200
        # GET 404
        rg2 = requests.get(f"{API}/builds/{bid}", headers=_auth_headers(user_a["token"]))
        assert rg2.status_code == 404


# ------------------------------------------------------------------
# Preview / games using build settings (incl. fouls)
# ------------------------------------------------------------------
class TestPreviewAndSettings:
    def test_preview_uses_build_stat_xp_and_fouls(self, user_a):
        bid = user_a["build_id"]
        h = _auth_headers(user_a["token"])
        # baseline: pts=20 -> 200, reb=5 -> 125, fouls=3 -> -30; result W -> 500
        payload = {"event_type":"Regular","difficulty":"Pro","result":"W",
                   "pts":20,"reb":5,"fouls":3}
        r = requests.post(f"{API}/builds/{bid}/games/preview", headers=h, json=payload)
        assert r.status_code == 200, r.text
        d = r.json()
        # 200 + 125 - 30 = 295; +500 win => 795
        assert d["base_stat_xp"] == 295
        assert d["result_xp"] == 500
        assert d["final_xp"] == 795

        # change pts xp to 20 via settings, preview should reflect
        rs = requests.put(f"{API}/builds/{bid}/settings", headers=h,
                          json={"stat_xp": {"pts": 20}})
        assert rs.status_code == 200
        assert rs.json()["stat_xp"]["pts"] == 20
        r2 = requests.post(f"{API}/builds/{bid}/games/preview", headers=h, json=payload)
        d2 = r2.json()
        # pts now 20*20=400; reb 125; fouls -30 => 495; +500 => 995
        assert d2["base_stat_xp"] == 495
        assert d2["final_xp"] == 995

    def test_settings_reset_restores_defaults(self, user_a):
        bid = user_a["build_id"]
        h = _auth_headers(user_a["token"])
        rr = requests.post(f"{API}/builds/{bid}/settings/reset", headers=h)
        assert rr.status_code == 200
        s = rr.json()
        assert s["stat_xp"]["pts"] == 10
        assert s["stat_xp"]["fouls"] == -10
        assert s["attr_costs"]["Finishing"]["1-25"] == 25
        assert s["badge_tier_cost"]["Legend"] == 500

    def test_add_game_persists_and_summary(self, user_a):
        bid = user_a["build_id"]
        h = _auth_headers(user_a["token"])
        r = requests.post(f"{API}/builds/{bid}/games", headers=h,
                          json={"event_type":"Regular","difficulty":"Pro","result":"W",
                                "pts":10,"fouls":2})
        assert r.status_code == 200
        gd = r.json()
        # 10*10 + 2*-10 + 500 = 580
        assert gd["final_xp"] == 580
        st = requests.get(f"{API}/builds/{bid}", headers=h).json()
        assert st["summary"]["xp_earned"] >= 580
        assert "build" in st  # renamed from profile


# ------------------------------------------------------------------
# Attribute & badge cost lookups via build settings (rating tiers)
# ------------------------------------------------------------------
class TestAttrAndBadgeCosts:
    def test_attr_upgrade_uses_build_setting(self, user_a):
        bid = user_a["build_id"]
        h = _auth_headers(user_a["token"])
        # reset settings so default applies (51-75 tier => cost 75 at level 50? Wait, level 50 -> tier "26-50")
        requests.post(f"{API}/builds/{bid}/settings/reset", headers=h)
        # add offseason for 3000 xp
        requests.post(f"{API}/builds/{bid}/games", headers=h, json={"event_type":"Offseason"})
        # Custom cost for Finishing tier 26-50 = 80
        rs = requests.put(f"{API}/builds/{bid}/settings", headers=h,
                          json={"attr_costs": {"Finishing": {"26-50": 80}}})
        assert rs.status_code == 200
        # Close Shot is Finishing, starting level 50 -> tier 26-50 -> cost 80
        ru = requests.put(f"{API}/builds/{bid}/attributes/Close Shot", headers=h, json={"delta": 1})
        assert ru.status_code == 200, ru.text
        a = ru.json()
        assert a["current_level"] == 51
        assert a["xp_spent"] == 80
        # refund -> level 50, refund cost of (51-1=50) which is tier 26-50 => 80
        rr = requests.put(f"{API}/builds/{bid}/attributes/Close Shot", headers=h, json={"delta": -1})
        assert rr.status_code == 200
        assert rr.json()["current_level"] == 50

    def test_badge_cumulative_uses_build_setting(self, user_a):
        bid = user_a["build_id"]
        h = _auth_headers(user_a["token"])
        # Set custom: Bronze=100, Silver=200, Gold=300 cumulatively => Gold xp_spent=600
        rs = requests.put(f"{API}/builds/{bid}/settings", headers=h,
                          json={"badge_tier_cost": {"Bronze":100,"Silver":200,"Gold":300}})
        assert rs.status_code == 200
        # ensure we have enough xp
        requests.post(f"{API}/builds/{bid}/games", headers=h, json={"event_type":"Offseason"})
        rb = requests.put(f"{API}/builds/{bid}/badges/Deadeye", headers=h, json={"tier":"Gold"})
        assert rb.status_code == 200, rb.text
        d = rb.json()
        assert d["current_tier"] == "Gold"
        assert d["xp_spent"] == 600


# ------------------------------------------------------------------
# Awards
# ------------------------------------------------------------------
class TestAwards:
    def test_award_merge_and_separate_seasons(self, user_a):
        bid = user_a["build_id"]
        h = _auth_headers(user_a["token"])
        r1 = requests.put(f"{API}/builds/{bid}/awards", headers=h,
                          json={"season":1, "mvp":True, "all_nba":"1st"})
        assert r1.status_code == 200
        awards = r1.json()
        s1 = next(a for a in awards if a["season"]==1)
        assert s1["mvp"] is True and s1["all_nba"] == "1st"

        # merge update season 1
        r2 = requests.put(f"{API}/builds/{bid}/awards", headers=h,
                          json={"season":1, "dpoy":True, "all_nba":"2nd"})
        awards = r2.json()
        s1 = next(a for a in awards if a["season"]==1)
        assert s1["mvp"] is True   # preserved
        assert s1["dpoy"] is True
        assert s1["all_nba"] == "2nd"  # replaced

        # new season => separate entry
        r3 = requests.put(f"{API}/builds/{bid}/awards", headers=h,
                          json={"season":2, "roy":True})
        awards = r3.json()
        assert len([a for a in awards if a["season"]==1]) == 1
        assert any(a["season"]==2 and a["roy"] is True for a in awards)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

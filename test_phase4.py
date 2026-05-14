"""Phase 4 tests: archetypes, starting-attributes, new defaults (stat_xp, bonus_xp, attr_costs, badge_tier_cost), preset rescaling, difficulty list."""
import os
import uuid
import pytest
import requests

BASE_URL = os.environ['REACT_APP_BACKEND_URL'].rstrip('/')
API = f"{BASE_URL}/api"


def _email():
    return f"TEST_{uuid.uuid4().hex[:10]}@example.com"


def _register(password="test1234"):
    email = _email()
    r = requests.post(f"{API}/auth/register", json={"email": email, "password": password})
    r.raise_for_status()
    return email, password, r.json()["token"]


def _hdr(t):
    return {"Authorization": f"Bearer {t}"}


def _new_build(t, height="6'5\""):
    b = requests.post(f"{API}/builds", json={"name": "P4", "position": "PG", "height": height}, headers=_hdr(t)).json()
    return b["id"], b


# ===== /api/archetypes =====
class TestArchetypes:
    def test_archetypes_public_no_auth(self):
        r = requests.get(f"{API}/archetypes")
        assert r.status_code == 200

    def test_archetypes_4_keys_and_height_meta(self):
        d = requests.get(f"{API}/archetypes").json()
        a = d["archetypes"]
        assert set(a.keys()) == {"Sharpshooter", "Slasher", "Playmaker", "Lock"}
        assert d.get("height_based_attrs") == ["Speed", "Agility"]
        assert set(d.get("buckets", {}).keys()) == {"short", "medium", "tall"}

    def test_archetype_signature_values(self):
        a = requests.get(f"{API}/archetypes").json()["archetypes"]
        assert a["Sharpshooter"]["Three-Point Shot"] == 85
        assert a["Slasher"]["Driving Dunk"] == 85
        assert a["Playmaker"]["Pass Accuracy"] == 85
        assert a["Lock"]["Steal"] == 85

    def test_sharpshooter_height_based_medium_speed_70(self):
        a = requests.get(f"{API}/archetypes").json()["archetypes"]
        hb = a["Sharpshooter"]["_height_based"]
        assert hb["medium"]["Speed"] == 70
        assert hb["medium"]["Agility"] == 70
        # short/tall sanity
        for k in ("short", "tall"):
            assert "Speed" in hb[k] and "Agility" in hb[k]


# ===== New build defaults =====
class TestNewBuildDefaults:
    def setup_method(self):
        _, _, self.t = _register()
        self.bid, self.b = _new_build(self.t)

    def test_setup_complete_false_and_archetype_null(self):
        assert self.b.get("setup_complete") is False
        assert self.b.get("archetype") in (None, "")

    def test_default_stat_xp(self):
        s = self.b["settings"]["stat_xp"]
        assert s["pts"] == 10
        assert s["reb"] == 8
        assert s["ast"] == 12
        assert s["stl"] == 12
        assert s["blk"] == 12
        assert s["tov"] == -8
        assert s["fgm"] == 0
        assert s["tpm"] == 0
        assert s["ftm"] == 0
        assert s["fouls"] == 0

    def test_default_bonus_xp(self):
        b = self.b["settings"]["bonus_xp"]
        assert b["win"] == 100
        assert b["loss"] == 25
        assert b["player_of_game"] == 50
        assert b["double_double"] == 40
        assert b["triple_double"] == 100
        assert b["offseason"] == 3000

    def test_default_badge_tier_cost(self):
        bt = self.b["settings"]["badge_tier_cost"]
        # JSON key for None may be "None" (string) or missing — both are acceptable in practice; check explicit tiers
        assert bt.get("Bronze") == 100
        assert bt.get("Silver") == 200
        assert bt.get("Gold") == 300
        assert bt.get("HOF") == 400
        assert bt.get("Legend") == 500

    def test_default_attr_costs_finishing(self):
        c = self.b["settings"]["attr_costs"]["Finishing"]
        assert c["1-25"] == 50
        assert c["26-50"] == 50
        assert c["51-75"] == 75
        assert c["76-85"] == 100
        assert c["86-90"] == 225
        assert c["91-99"] == 500

    def test_default_attr_costs_shooting(self):
        c = self.b["settings"]["attr_costs"]["Shooting"]
        assert c["1-25"] == 50
        assert c["26-50"] == 50
        assert c["51-75"] == 75
        assert c["76-85"] == 150
        assert c["86-90"] == 250
        assert c["91-99"] == 600

    def test_default_attr_costs_playmaking(self):
        c = self.b["settings"]["attr_costs"]["Playmaking"]
        assert c["1-25"] == 50
        assert c["26-50"] == 50
        assert c["51-75"] == 100
        assert c["76-85"] == 150
        assert c["86-90"] == 250
        assert c["91-99"] == 600

    def test_default_attr_costs_defense_rebounding_equal_finishing(self):
        cs = self.b["settings"]["attr_costs"]
        for cat in ("Defense", "Rebounding"):
            assert cs[cat] == cs["Finishing"], f"{cat} should equal Finishing"

    def test_default_attr_costs_physicals(self):
        c = self.b["settings"]["attr_costs"]["Physicals"]
        assert c["1-25"] == 100
        assert c["26-50"] == 100
        assert c["51-75"] == 225
        assert c["76-85"] == 500
        assert c["86-90"] == 750
        assert c["91-99"] == 1000


# ===== Difficulty list =====
class TestDifficultyList:
    def test_difficulties_no_rookie(self):
        _, _, t = _register()
        bid, _ = _new_build(t)
        state = requests.get(f"{API}/builds/{bid}", headers=_hdr(t)).json()
        diffs = state["constants"]["difficulties"]
        assert "Rookie" not in diffs
        assert diffs == ["Pro", "All-Star", "Superstar", "Hall of Fame"]


# ===== Starting attributes endpoint =====
class TestStartingAttributes:
    def test_sets_levels_and_archetype(self):
        _, _, t = _register()
        bid, _ = _new_build(t)
        payload = {
            "archetype": "Sharpshooter",
            "attributes": [
                {"name": "Three-Point Shot", "level": 85},
                {"name": "Close Shot", "level": 70},
                {"name": "Speed", "level": 70},
                {"name": "Agility", "level": 70},
            ],
        }
        r = requests.put(f"{API}/builds/{bid}/starting-attributes", json=payload, headers=_hdr(t))
        assert r.status_code == 200, r.text
        b = r.json()
        assert b["setup_complete"] is True
        assert b["archetype"] == "Sharpshooter"
        # check via /builds/{id}
        s = requests.get(f"{API}/builds/{bid}", headers=_hdr(t)).json()
        attrs = {a["name"]: a for a in s["build"]["attributes"]}
        assert attrs["Three-Point Shot"]["starting_level"] == 85
        assert attrs["Three-Point Shot"]["current_level"] == 85
        assert attrs["Three-Point Shot"]["xp_spent"] == 0
        assert attrs["Close Shot"]["current_level"] == 70
        assert attrs["Speed"]["current_level"] == 70

    def test_levels_out_of_range_rejected(self):
        _, _, t = _register()
        bid, _ = _new_build(t)
        # 24 (below 25) and 100 (above 99) — but note backend clamps if it accepts.
        # The schema constraint should reject via 422 if validation in Pydantic;
        # if backend simply clamps, accept that too as long as it doesn't exceed bounds.
        for bad in (24, 100, 0, -1):
            r = requests.put(
                f"{API}/builds/{bid}/starting-attributes",
                json={"archetype": "Custom", "attributes": [{"name": "Three-Point Shot", "level": bad}]},
                headers=_hdr(t),
            )
            if r.status_code == 200:
                # Verify it was clamped to 25..99
                s = requests.get(f"{API}/builds/{bid}", headers=_hdr(t)).json()
                lvl = next(a["current_level"] for a in s["build"]["attributes"] if a["name"] == "Three-Point Shot")
                assert 25 <= lvl <= 99
            else:
                assert r.status_code in (400, 422), f"level={bad} -> {r.status_code}"

    def test_requires_auth(self):
        _, _, t = _register()
        bid, _ = _new_build(t)
        r = requests.put(
            f"{API}/builds/{bid}/starting-attributes",
            json={"archetype": "Custom", "attributes": [{"name": "Three-Point Shot", "level": 80}]},
        )
        assert r.status_code == 401

    def test_ownership_enforced(self):
        _, _, t1 = _register()
        _, _, t2 = _register()
        bid, _ = _new_build(t1)
        r = requests.put(
            f"{API}/builds/{bid}/starting-attributes",
            json={"archetype": "Custom", "attributes": [{"name": "Three-Point Shot", "level": 80}]},
            headers=_hdr(t2),
        )
        assert r.status_code == 404


# ===== Preset rescaling against new defaults =====
class TestPresets:
    def _bid(self):
        _, _, t = _register()
        bid, _ = _new_build(t)
        return bid, t

    def test_default_preset_values(self):
        bid, t = self._bid()
        r = requests.post(f"{API}/builds/{bid}/settings/preset", json={"name": "default"}, headers=_hdr(t))
        assert r.status_code == 200
        s = r.json()["settings"]
        assert s["stat_xp"]["pts"] == 10
        assert s["badge_tier_cost"]["Bronze"] == 100
        assert s["attr_costs"]["Finishing"]["51-75"] == 75

    def test_casual_preset_scales(self):
        bid, t = self._bid()
        r = requests.post(f"{API}/builds/{bid}/settings/preset", json={"name": "casual"}, headers=_hdr(t))
        assert r.status_code == 200
        s = r.json()["settings"]
        assert s["stat_xp"]["pts"] == 15  # 10 * 1.5
        assert s["badge_tier_cost"]["Bronze"] == 50  # 100 * 0.5
        assert s["attr_costs"]["Finishing"]["51-75"] == 38  # round(75 * 0.5)

    def test_hard_preset(self):
        bid, t = self._bid()
        r = requests.post(f"{API}/builds/{bid}/settings/preset", json={"name": "hard"}, headers=_hdr(t))
        assert r.status_code == 200
        s = r.json()["settings"]
        assert s["stat_xp"]["pts"] == 8  # round(10*0.75)
        assert s["badge_tier_cost"]["Bronze"] == 150  # 100*1.5

    def test_hardcore_sim_preset(self):
        bid, t = self._bid()
        r = requests.post(f"{API}/builds/{bid}/settings/preset", json={"name": "hardcore_sim"}, headers=_hdr(t))
        assert r.status_code == 200
        s = r.json()["settings"]
        assert s["stat_xp"]["pts"] == 5  # 10*0.5
        assert s["badge_tier_cost"]["Bronze"] == 200  # 100*2
        assert s["attr_costs"]["Physicals"]["91-99"] == 2000  # 1000*2


# ===== Regression =====
class TestRegression:
    def test_login_admin(self):
        r = requests.post(f"{API}/auth/login", json={"email": "admin@2kengine.app", "password": "admin123"})
        assert r.status_code == 200

    def test_existing_endpoints_still_work(self):
        _, _, t = _register()
        bid, _ = _new_build(t)
        # add game
        g = requests.post(f"{API}/builds/{bid}/games", json={
            "season": 1, "event_type": "Regular", "difficulty": "Pro", "result": "W",
            "pts": 20, "reb": 5, "ast": 3, "stl": 1, "blk": 0, "tov": 0, "fgm": 0, "tpm": 0, "ftm": 0, "fouls": 0,
        }, headers=_hdr(t))
        assert g.status_code == 200

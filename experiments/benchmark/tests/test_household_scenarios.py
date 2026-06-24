from energybridge.roleplay.households import (
    build_household_persona,
    list_household_ids,
    load_household_config,
    load_household_member_personas,
)
from experiments.benchmark.run_multi_user_household import (
    IndependentMemberRoleplay,
    _build_physical_household_persona,
    _make_roleplay_callbacks,
)
from experiments.benchmark.user_pref_scorer import StrategyPreference


REQUIRED_SHARED_APPLIANCES = ("ac", "washer", "dryer", "dishwasher", "water_heater", "ev")


def test_fixed_households_are_reproducible_large_users():
    household_ids = list_household_ids()
    assert {
        "household_s1_dual_commuter_standard",
        "household_s2_multigeneration_caregiver",
        "household_s3_hybrid_work_from_home",
        "household_s4_ev_commuter_flexible",
        "household_s5_shared_roommates_irregular",
    }.issubset(set(household_ids))

    for household_id in household_ids:
        household = load_household_config(household_id)
        members = load_household_member_personas(household)
        aggregate = build_household_persona(household, members, days=7)

        assert aggregate["id"] == household_id
        assert len(aggregate["members"]) == len(members)
        assert aggregate["meta"]["calendar_merge_policy"] == "union_home_occupancy_max"

        for appliance in REQUIRED_SHARED_APPLIANCES:
            assert aggregate["appliances"][appliance]["present"] is True

        occupancy = aggregate["calendar"]["household_occupancy_hourly"]
        assert len(occupancy) == 7
        assert all(len(day) == 24 for day in occupancy)
        assert all(0.0 <= value <= 1.0 for day in occupancy for value in day)


def test_multi_user_scoring_uses_each_members_own_preference():
    household = load_household_config("household_s3_hybrid_work_from_home")
    members = load_household_member_personas(household)[:2]
    roleplay = IndependentMemberRoleplay(household, members)

    def fake_get_pref(building, event_index, vpp_context, past_events, persona, human_mode=False):
        member_id = persona["household_member"]["member_id"]
        return StrategyPreference(
            f"preference-from-{member_id}",
            {"selected_strategy": {"id": member_id, "label": f"choice-{member_id}"}},
        )

    roleplay.choose_strategy(
        orig_get_pref=fake_get_pref,
        building="family",
        event_index=1,
        vpp_context={"trigger_h": 18.0, "end_h": 19.0},
        past_events=[],
    )

    seen_preferences = {}

    def fake_score(**kwargs):
        member_id = kwargs["persona"]["household_member"]["member_id"]
        seen_preferences[member_id] = kwargs["user_preference_text"]
        return {
            "score": 4,
            "comfort_score": 4,
            "energy_score": 4,
            "vpp_score": 4,
            "label": "satisfied",
            "comment": "ok",
            "source": "roleplay_llm",
        }

    aggregate = roleplay.score_event(
        orig_score=fake_score,
        building="family",
        method="EnergyBridge",
        mean_temp_c=25.0,
        pmv_ok_fraction=1.0,
        energy_kwh_per_day=20.0,
        agent_setpoint_c=25.0,
        event_index=1,
        user_preference_text="aggregate-controller-feedback",
        agent_reason="test",
        kwargs={},
    )

    assert aggregate["source"] == "multi_user_independent_mean"
    assert seen_preferences == {
        member["household_member"]["member_id"]: f"preference-from-{member['household_member']['member_id']}"
        for member in members
    }
    assert "controller_feedback" in aggregate
    assert all(member["household_member"]["member_id"] in aggregate["controller_feedback"] for member in members)


def test_household_agent_context_exposes_members_calendars_and_service_contract():
    household = load_household_config("household_s1_dual_commuter_standard")
    members = load_household_member_personas(household)
    physical = _build_physical_household_persona(household, members, days=7)
    agent_context = physical["llm_prompts"]["agent_context"]

    for token in ("father", "mother", "child", "elder"):
        assert token in agent_context
    for token in ("washer", "dryer", "dishwasher", "water_heater", "EV"):
        assert token in agent_context
    assert "skip=true is allowed only" in agent_context
    assert "All household member calendars visible to the controller" in agent_context
    assert "Day 1" in agent_context


def test_member_roleplay_uses_household_shared_appliances_without_losing_ac_preferences():
    household = load_household_config("household_s1_dual_commuter_standard")
    members = load_household_member_personas(household)
    roleplay = IndependentMemberRoleplay(household, members)
    original_ac = members[0].get("appliances", {}).get("ac", {})

    persona = roleplay._persona_with_context(members[0])

    assert persona["appliances"]["washer"]["present"] is True
    assert persona["appliances"]["dryer"]["present"] is True
    assert persona["appliances"]["dishwasher"]["present"] is True
    assert persona["appliances"]["water_heater"]["present"] is True
    assert persona["appliances"]["ev"]["present"] is True
    if original_ac:
        assert persona["appliances"]["ac"] == original_ac


def test_multi_user_callbacks_do_not_mutate_global_scorer_functions():
    import experiments.benchmark.user_pref_scorer as scorer

    household = load_household_config("household_s3_hybrid_work_from_home")
    members = load_household_member_personas(household)[:2]
    roleplay = IndependentMemberRoleplay(household, members)
    original_get = scorer.get_user_preference_input
    original_score = scorer.score_user_preference

    _make_roleplay_callbacks(roleplay, original_get, original_score)

    assert scorer.get_user_preference_input is original_get
    assert scorer.score_user_preference is original_score

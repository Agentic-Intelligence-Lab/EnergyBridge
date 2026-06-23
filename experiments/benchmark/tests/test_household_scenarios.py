from energybridge.roleplay.households import (
    build_household_persona,
    list_household_ids,
    load_household_config,
    load_household_member_personas,
)


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

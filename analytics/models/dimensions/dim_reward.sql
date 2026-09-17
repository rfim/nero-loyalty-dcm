-- Shell dimension: no reward master source exists (contracts/loyalty.yaml's
-- transactions/loyalty_events datasets carry reward_id but no reward catalog).
-- Standard members only — do not invent reward names, types or point values.
select '-1' as reward_key, null as reward_id, 'Unknown' as reward_name, 'Unknown' as reward_type, null as points_required
union all
select '-2' as reward_key, null as reward_id, 'Not Applicable' as reward_name, 'Not Applicable' as reward_type, null as points_required

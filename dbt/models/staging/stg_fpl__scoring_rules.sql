-- Points per scoring event, per position, from the API's own rules (D25). One row per rule per
-- position. Rules that are a single number in the API apply to every position.
with latest as (
    select payload->'game_config'->'scoring' as scoring
    from {{ source('fpl_raw', 'bootstrap_static') }}
    where dt = (select max(dt) from {{ source('fpl_raw', 'bootstrap_static') }})
),

rules as (
    select unnest(json_keys(scoring)) as rule, scoring
    from latest
),

positions as (
    select position_code from {{ ref('stg_fpl__positions') }}
)

select
    r.rule,
    p.position_code,
    case
        when json_type(json_extract(r.scoring, '$."' || r.rule || '"')) = 'OBJECT'
            then json_extract_string(r.scoring, '$."' || r.rule || '"."' || p.position_code || '"')
        else json_extract_string(r.scoring, '$."' || r.rule || '"')
    end::int as points
from rules r
cross join positions p

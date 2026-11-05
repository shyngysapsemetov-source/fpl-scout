-- Guards the third error in the ledger: a timezone shift between the API's UTC strings and
-- staging. Every staged kickoff, shown in UTC, must equal the raw `kickoff_time` exactly.
-- Fails on any machine, whatever its local timezone.
with raw as (
    select (f->>'id')::int as fixture_id, f->>'kickoff_time' as raw_kickoff
    from (
        select unnest(cast(payload as json[])) as f
        from {{ source('fpl_raw', 'fixtures') }}
        where dt = (select max(dt) from {{ source('fpl_raw', 'fixtures') }})
    )
)

select s.fixture_id, s.kickoff_at, r.raw_kickoff
from {{ ref('stg_fpl__fixtures') }} s
join raw r using (fixture_id)
where strftime(timezone('UTC', s.kickoff_at), '%Y-%m-%dT%H:%M:%SZ') is distinct from r.raw_kickoff

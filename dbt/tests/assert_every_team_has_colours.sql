-- D65: every current team needs a badge colour; a newly promoted club fails this until its
-- row is added to seeds/team_colours.csv.
select t.team_code, t.team_short_name
from {{ ref('stg_fpl__teams') }} t
left join {{ ref('team_colours') }} c using (team_code)
where c.team_code is null

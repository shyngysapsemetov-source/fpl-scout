{#- D118/D119: P(Y >= thr) for a negative binomial (NB2) with mean `mu` and size `n`
    (variance mu + mu^2 / n), as 1 - the sum of the pmf below thr. `thr` is a column or value of
    at most `max_k`. -#}
{%- macro nb_sf(mu, n, thr, max_k=12) -%}
    (1 - (0
    {%- for k in range(max_k) %}
        + case when {{ k }} < {{ thr }} then exp(lgamma({{ k }} + {{ n }}) - lgamma({{ n }}) - lgamma({{ k + 1 }})
               + {{ n }} * ln({{ n }} / ({{ n }} + {{ mu }})) + {{ k }} * ln({{ mu }} / ({{ n }} + {{ mu }}))) else 0 end
    {%- endfor %}))
{%- endmacro %}

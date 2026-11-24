# The reusable engine: generic stages (lake, parse, chunk, embed, index, llm, service, agent, eval) behind
# small interfaces. It never imports a domain, Airflow, Dash, or agent_data's own config — settings come in
# as plain objects — so another project can lift this folder and plug in its own domain.

# The lake layer: raw objects plus Delta tables in one place (a local folder, s3:// or abfss://).
# Takes in bytes and row dicts; gives them back. base.py is the interface; delta_store.py is the delta-rs backend.
# To add a backend, write one module implementing LakeStore and register it in agent_data/platform.py.

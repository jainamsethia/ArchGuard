# module_a.py
# Intentional boundary violation: core importing from api
import src.api.module_b


def do_core_stuff():
    return src.api.module_b.do_api_stuff()

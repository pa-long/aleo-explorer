from io import BytesIO

import aleo
from starlette.requests import Request
from starlette.responses import JSONResponse

from api.utils import async_check_sync
from db import Database
from node.types import Program, Plaintext, Value, PlaintextType, LiteralPlaintextType, LiteralPlaintext, \
    Literal, StructPlaintextType


@async_check_sync
async def mapping_route(request: Request):
    db: Database = request.app.state.db
    version = request.path_params["version"]
    program_id = request.path_params["program_id"]
    mapping = request.path_params["mapping"]
    key = request.path_params["key"]
    try:
        program = Program.load(BytesIO(await db.get_program(program_id)))
    except:
        return JSONResponse({"error": "Program not found"}, status_code=404)
    if mapping not in program.mappings:
        return JSONResponse({"error": "Mapping not found"}, status_code=404)
    map_key_type = program.mappings[mapping].key.plaintext_type
    if map_key_type.type == PlaintextType.Type.Literal:
        map_key_type: LiteralPlaintextType
        primitive_type = map_key_type.literal_type.primitive_type
        try:
            key = primitive_type.loads(key)
        except:
            return JSONResponse({"error": "Invalid key"}, status_code=400)
        key = LiteralPlaintext(literal=Literal(type_=Literal.reverse_primitive_type_map[primitive_type], primitive=key))
    elif map_key_type.type == PlaintextType.Type.Struct:
        map_key_type: StructPlaintextType
        return JSONResponse({"error": "Struct keys not supported yet"}, status_code=500)
    else:
        return JSONResponse({"error": "Unknown key type"}, status_code=500)
    mapping_id = aleo.get_mapping_id(program_id, mapping)
    key_id = aleo.get_key_id(mapping_id, key.dump())
    value = await db.get_mapping_value(program_id, mapping, key_id)
    if value is None:
        return JSONResponse(None)
    return JSONResponse(str(Value.load(BytesIO(value))))

@async_check_sync
async def mapping_list_route(request: Request):
    db: Database = request.app.state.db
    version = request.path_params["version"]
    program_id = request.path_params["program_id"]
    program = await db.get_program(program_id)
    if not program:
        return JSONResponse({"error": "Program not found"}, status_code=404)
    program = Program.load(BytesIO(program))
    mappings = program.mappings
    return JSONResponse(list(map(str, mappings.keys())))

@async_check_sync
async def mapping_value_list_route(request: Request):
    db: Database = request.app.state.db
    version = request.path_params["version"]
    program_id = request.path_params["program_id"]
    mapping = request.path_params["mapping"]
    program = await db.get_program(program_id)
    if not program:
        return JSONResponse({"error": "Program not found"}, status_code=404)
    program = Program.load(BytesIO(program))
    mappings = program.mappings
    if mapping not in mappings:
        return JSONResponse({"error": "Mapping not found"}, status_code=404)
    mapping_id = aleo.get_mapping_id(program_id, mapping)
    mapping_cache = await db.get_mapping_cache(mapping_id)
    res = []
    for item in mapping_cache:
        res.append({
            "index": item["index"],
            "key": str(Plaintext.load(BytesIO(item["key"]))),
            "value": str(Value.load(BytesIO(item["value"]))),
            "key_id": item["key_id"],
            "value_id": item["value_id"],
        })
    return JSONResponse(res)
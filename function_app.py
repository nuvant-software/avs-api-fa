import azure.functions as func
import logging, os, json
from azure.cosmos import CosmosClient

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

@app.route(route="filter_cars", methods=["POST"])
def filter_cars(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("filter_cars triggered")
    # --- init Cosmos ---
    cs = os.environ["COSMOS_DB_CONNECTION_STRING"]
    client = CosmosClient.from_connection_string(cs)
    container = client.get_database_client("avs-db").get_container_client("avs-cs")

    # --- parse filters + includeItems ---
    try:
        body = req.get_json()
    except:
        return func.HttpResponse("Invalid JSON", status_code=400)
    filters      = body.get("filters", {})
    includeItems = body.get("includeItems", False)

    # --- hiërarchie checks ---
    if filters.get("model") and not filters.get("brand"):
        return func.HttpResponse("Model zonder merk.", status_code=400)
    if filters.get("variant") and not filters.get("model"):
        return func.HttpResponse("Variant zonder model.", status_code=400)

    # --- bouw WHERE en params voor count/items ---
    where, params = ["1=1"], []
    def add_list(f):
        v = filters.get(f)
        if not v: return
        vals = v if isinstance(v, list) else [x.strip() for x in v.split(",")]
        phs  = ",".join(f"@{f}_{i}" for i in range(len(vals)))
        where.append(f"c.car_overview.{f} IN ({phs})")
        for i, x in enumerate(vals):
            params.append({"name": f"@{f}_{i}", "value": x})

    for f in ("brand","model","variant"):
        add_list(f)

    base_where = " AND ".join(where)

    result = {}
    try:
        # --- 0) totaal aantal matching items ---
        q_count = f"SELECT VALUE COUNT(1) FROM c WHERE {base_where}"
        if params:
            total = list(container.query_items(
                query=q_count,
                parameters=params,
                enable_cross_partition_query=True
            ))[0]
        else:
            total = list(container.query_items(
                query=q_count,
                enable_cross_partition_query=True
            ))[0]
        result["totalCount"] = total

        # --- 1) eventueel items ophalen ---
        if includeItems:
            q_items = f"SELECT * FROM c WHERE {base_where}"
            if params:
                items = list(container.query_items(
                    query=q_items,
                    parameters=params,
                    enable_cross_partition_query=True
                ))
            else:
                items = list(container.query_items(
                    query=q_items,
                    enable_cross_partition_query=True
                ))
            result["items"] = items

        # --- 2) facets: ophalen + dedupliceren in Python ---
        facets = {}

        # 2a) brands: altijd volledig
        all_brands = list(container.query_items(
            query="SELECT VALUE c.car_overview.brand FROM c",
            enable_cross_partition_query=True
        ))
        facets["brands"] = {
            "options": sorted(set(all_brands))
        }

        # 2b) models: alleen als er een brand-filter is
        if filters.get("brand"):
            brand_params = [p for p in params if p["name"].startswith("@brand_")]
            phs = ",".join(p["name"] for p in brand_params)
            q_mod = f"SELECT VALUE c.car_overview.model FROM c WHERE c.car_overview.brand IN ({phs})"
            models = list(container.query_items(
                query=q_mod,
                parameters=brand_params,
                enable_cross_partition_query=True
            ))
            facets["models"] = { "options": sorted(set(models)) }
        else:
            facets["models"] = { "options": [] }

        # 2c) variants: alleen als er een model-filter is
        if filters.get("model"):
            model_params = [p for p in params if p["name"].startswith("@model_")]
            phs = ",".join(p["name"] for p in model_params)
            q_var = f"SELECT VALUE c.car_overview.variant FROM c WHERE c.car_overview.model IN ({phs})"
            variants = list(container.query_items(
                query=q_var,
                parameters=model_params,
                enable_cross_partition_query=True
            ))
            facets["variants"] = { "options": sorted(set(variants)) }
        else:
            facets["variants"] = { "options": [] }

        result["facets"] = facets

        # --- 3) price-range: min = 0, max = duurste auto ---
        max_price = list(container.query_items(
            query="SELECT VALUE MAX(c.car_overview.price) FROM c",
            enable_cross_partition_query=True
        ))[0] or 0
        result["ranges"] = { "price": [0, max_price] }

        return func.HttpResponse(
            body=json.dumps(result, ensure_ascii=False),
            status_code=200,
            mimetype="application/json"
        )

    except Exception as e:
        logging.error(f"Filter error: {e}")
        return func.HttpResponse("Server error", status_code=500)

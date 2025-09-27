import azure.functions as func
import logging, os, json
from azure.cosmos import CosmosClient

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

@app.route(route="filter_cars", methods=["POST"])
def filter_cars(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("filter_cars triggered")

    # --- init Cosmos ---
    try:
        cs = os.environ["COSMOS_DB_CONNECTION_STRING"]
        client = CosmosClient.from_connection_string(cs)
        container = client.get_database_client("avs-db").get_container_client("avs-cs")
    except Exception as e:
        logging.error(f"Cosmos init error: {e}")
        return func.HttpResponse("Server init error", status_code=500)

    # --- parse filters + includeItems ---
    try:
        body = req.get_json()
    except Exception:
        return func.HttpResponse("Invalid JSON", status_code=400)

    filters      = body.get("filters", {}) or {}
    includeItems = bool(body.get("includeItems", False))

    # --- read legacy fields (flat) ---
    legacy_brands   = filters.get("brand")   or filters.get("brands")   or []
    legacy_models   = filters.get("model")   or []
    legacy_variants = filters.get("variant") or []
    # --- read scoped fields (recommended) ---
    scoped_models_by_brand = filters.get("models_by_brand") or {}
    scoped_vars_by_bm      = filters.get("variants_by_brand_model") or {}

    price_min = filters.get("price_min", None)
    price_max = filters.get("price_max", None)

    # --- normalize lists ---
    def norm_list(v):
        if v is None: return []
        if isinstance(v, list): return v
        return [x.strip() for x in str(v).split(",") if x.strip()]

    legacy_brands   = norm_list(legacy_brands)
    legacy_models   = norm_list(legacy_models)
    legacy_variants = norm_list(legacy_variants)

    # --- decide mode: scoped vs legacy ---
    use_scoped = bool(scoped_models_by_brand or scoped_vars_by_bm)

    # -------------- WHERE builder --------------
    params = []
    where_clauses = []

    def add_param(name, value):
        pname = f"@{name}_{len(params)}"
        params.append({"name": pname, "value": value})
        return pname

    if use_scoped:
        # Build OR over brands; each brand has its own constraints
        # Determine the set of brands to include
        brands_set = set(legacy_brands)  # allow sending both brands and scoped maps
        brands_set.update(scoped_models_by_brand.keys())
        brands_set.update(scoped_vars_by_bm.keys())

        if not brands_set:
            # If no brands explicitly given but scoped maps exist (edge case), derive from maps' keys
            brands_set = set(scoped_models_by_brand.keys()) | set(scoped_vars_by_bm.keys())

        brand_groups = []
        for b in sorted(brands_set):
            b_param = add_param("brand", b)
            brand_clause = [f"c.car_overview.brand = {b_param}"]

            # union of models explicitly chosen for this brand and models that appear in variants map
            models_for_b = set(scoped_models_by_brand.get(b, []) or [])
            models_from_variants = set((scoped_vars_by_bm.get(b) or {}).keys())
            model_union = sorted(models_for_b | models_from_variants)

            if model_union:
                # For each model, optionally scope variants
                per_model_clauses = []
                for m in model_union:
                    m_p = add_param("model", m)
                    # variants for this brand+model (if any)
                    variants_for_bm = norm_list((scoped_vars_by_bm.get(b) or {}).get(m, []))
                    if variants_for_bm:
                        v_params = [add_param("variant", v) for v in variants_for_bm]
                        v_in = ",".join(v_params)
                        per_model_clauses.append(f"(c.car_overview.model = {m_p} AND c.car_overview.variant IN ({v_in}))")
                    else:
                        # no variant restriction for this model
                        per_model_clauses.append(f"(c.car_overview.model = {m_p})")
                brand_clause.append("(" + " OR ".join(per_model_clauses) + ")")
            else:
                # No models specified for this brand; brand alone is enough (all models/variants for this brand)
                pass

            brand_groups.append("(" + " AND ".join(brand_clause) + ")")

        if brand_groups:
            where_clauses.append("(" + " OR ".join(brand_groups) + ")")
        else:
            # No brand constraints at all -> match all (will still apply price later)
            where_clauses.append("1=1")

    else:
        # Legacy global filtering (brand/model/variant apply to ALL brands)
        def add_in(field, values):
            values = norm_list(values)
            if not values:
                return
            pnames = [add_param(field, v) for v in values]
            where_clauses.append(f"c.car_overview.{field} IN ({','.join(pnames)})")

        add_in("brand",   legacy_brands)
        add_in("model",   legacy_models)
        add_in("variant", legacy_variants)
        if not where_clauses:
            where_clauses.append("1=1")

    # Price filter (applies in both modes)
    if isinstance(price_min, (int, float)):
        pmin = add_param("pmin", price_min)
        where_clauses.append(f"c.car_overview.price >= {pmin}")
    if isinstance(price_max, (int, float)):
        pmax = add_param("pmax", price_max)
        where_clauses.append(f"c.car_overview.price <= {pmax}")

    base_where = " AND ".join(where_clauses)

    # -------------- Execute queries --------------
    result = {}
    try:
        # 0) Count
        q_count = f"SELECT VALUE COUNT(1) FROM c WHERE {base_where}"
        total = list(container.query_items(
            query=q_count,
            parameters=params if params else None,
            enable_cross_partition_query=True
        ))[0]
        result["totalCount"] = total

        # 1) Items
        if includeItems:
            q_items = f"SELECT * FROM c WHERE {base_where}"
            items = list(container.query_items(
                query=q_items,
                parameters=params if params else None,
                enable_cross_partition_query=True
            ))
            result["items"] = items

        # 2) Facets
        facets = {}

        # 2a) brands: always full list (distinct in Python)
        all_brands = list(container.query_items(
            query="SELECT VALUE c.car_overview.brand FROM c",
            enable_cross_partition_query=True
        ))
        facets["brands"] = {"options": sorted(set(all_brands))}

        # 2b) models facet: if brand(s) selected (scoped or legacy), return models within those brands
        brands_for_models = []
        if use_scoped:
            brands_for_models = sorted(set(brands_set))
        else:
            brands_for_models = legacy_brands

        if brands_for_models:
            # build IN list for brands
            b_params = [add_param("facet_b", b) for b in brands_for_models]
            q_mod = f"SELECT VALUE c.car_overview.model FROM c WHERE c.car_overview.brand IN ({','.join(b_params)})"
            models = list(container.query_items(
                query=q_mod,
                parameters=params,
                enable_cross_partition_query=True
            ))
            facets["models"] = {"options": sorted(set(models))}
        else:
            facets["models"] = {"options": []}

        # 2c) variants facet:
        # - If scoped: use the chosen (brand, model) pairs to fetch variants
        # - Else legacy: if there is a global model filter, fetch variants within those models
        if use_scoped:
            # Build OR of (brand=model IN (...)) groups
            vm_groups = []
            vm_local_params = []
            for b in sorted(set(brands_set)):
                models_for_b = set(scoped_models_by_brand.get(b, []) or [])
                # Also include models that appear only in variants map
                models_for_b |= set((scoped_vars_by_bm.get(b) or {}).keys())
                if not models_for_b:
                    continue
                b_p = add_param("facet_b2", b)
                m_ps = [add_param("facet_m2", m) for m in sorted(models_for_b)]
                vm_groups.append(f"(c.car_overview.brand = {b_p} AND c.car_overview.model IN ({','.join(m_ps)}))")
            facet_where = " OR ".join(vm_groups)
            if facet_where:
                q_var = f"SELECT VALUE c.car_overview.variant FROM c WHERE {facet_where}"
                variants = list(container.query_items(
                    query=q_var,
                    parameters=params,
                    enable_cross_partition_query=True
                ))
                facets["variants"] = {"options": sorted(set(variants))}
            else:
                facets["variants"] = {"options": []}
        else:
            if legacy_models:
                m_params = [add_param("facet_m", m) for m in legacy_models]
                q_var = f"SELECT VALUE c.car_overview.variant FROM c WHERE c.car_overview.model IN ({','.join(m_params)})"
                variants = list(container.query_items(
                    query=q_var,
                    parameters=params,
                    enable_cross_partition_query=True
                ))
                facets["variants"] = {"options": sorted(set(variants))}
            else:
                facets["variants"] = {"options": []}

        result["facets"] = facets

        # 3) price range (max price for UI slider upper bound)
        max_price = list(container.query_items(
            query="SELECT VALUE MAX(c.car_overview.price) FROM c",
            enable_cross_partition_query=True
        ))[0] or 0
        result["ranges"] = {"price": [0, max_price]}

        return func.HttpResponse(
            body=json.dumps(result, ensure_ascii=False),
            status_code=200,
            mimetype="application/json"
        )

    except Exception as e:
        logging.exception("Filter error")
        return func.HttpResponse("Server error", status_code=500)

import azure.functions as func
import logging
import os
import json
from azure.cosmos import CosmosClient

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

@app.route(route="filter_cars", methods=["POST"])
def filter_cars(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('Function triggered: filter_cars')

    connection_string = os.environ["COSMOS_DB_CONNECTION_STRING"]
    database_name = "avs-db"
    container_name = "avs-cs"

    try:
        # JSON body ophalen
        filters = req.get_json()
    except:
        return func.HttpResponse("Invalid JSON", status_code=400)

    # Partition key constraints
    brand = filters.get("brand")
    model = filters.get("model")
    variant = filters.get("variant")

    # Hiërarchie afdwingen
    if model and not brand:
        return func.HttpResponse("Model opgegeven zonder merk.", status_code=400)
    if variant and not model:
        return func.HttpResponse("Variant opgegeven zonder model.", status_code=400)

    # Begin query
    query = "SELECT * FROM c WHERE 1=1"
    params = []

    def add_list_filter(field, values):
        if values:
            placeholders = ', '.join([f"@{field}_{i}" for i in range(len(values))])
            query_part = f" AND c.car_overview.{field} IN ({placeholders})"
            query_parts.append(query_part)
            for i, val in enumerate(values):
                params.append({"name": f"@{field}_{i}", "value": val})

    query_parts = []

    add_list_filter("brand", brand)
    add_list_filter("model", model)
    add_list_filter("variant", variant)
    add_list_filter("carrosserie", filters.get("carrosserie"))
    add_list_filter("transmission", filters.get("transmission"))
    add_list_filter("doors", filters.get("doors"))

    # Numerieke filters
    if filters.get("price_max"):
        query_parts.append(" AND c.car_overview.price <= @price_max")
        params.append({"name": "@price_max", "value": filters["price_max"]})
    if filters.get("pk_min"):
        query_parts.append(" AND c.car_overview.pk >= @pk_min")
        params.append({"name": "@pk_min", "value": filters["pk_min"]})
    if filters.get("pk_max"):
        query_parts.append(" AND c.car_overview.pk <= @pk_max")
        params.append({"name": "@pk_max", "value": filters["pk_max"]})
    if filters.get("mileage_max"):
        query_parts.append(" AND c.car_overview.mileage <= @mileage_max")
        params.append({"name": "@mileage_max", "value": filters["mileage_max"]})

    # Final query string
    query += ''.join(query_parts)

    try:
        client = CosmosClient.from_connection_string(connection_string)
        database = client.get_database_client(database_name)
        container = database.get_container_client(container_name)

        items = list(container.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True  # omdat je filtert op meerdere brands
        ))

        return func.HttpResponse(
            body=json.dumps(items),
            status_code=200,
            mimetype="application/json"
        )

    except Exception as e:
        logging.error(f"Error querying Cosmos DB: {str(e)}")
        return func.HttpResponse(
            json.dumps({"error": "Failed to fetch data"}),
            status_code=500,
            mimetype="application/json"
        )

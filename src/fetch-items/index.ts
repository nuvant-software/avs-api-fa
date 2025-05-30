import { app, HttpRequest, HttpResponseInit, InvocationContext } from "@azure/functions";
import { CosmosClient } from "@azure/cosmos";

const DB_ID = "avs-db";
const CONTAINER_ID = "avs-cs";

export async function fetchItems(req: HttpRequest, ctx: InvocationContext): Promise<HttpResponseInit> {
  ctx.log("Function fetch-items triggered.");

  const conn = process.env.COSMOS_DB_CONNECTION;
  if (!conn) {
    ctx.log("COSMOS_DB_CONNECTION missing");
    return { status: 500, body: "DB connection not configured" };
  }

  const client = new CosmosClient(conn);
  const container = client.database(DB_ID).container(CONTAINER_ID);

  const q = req.query;
  let query = "SELECT * FROM c WHERE 1=1";
  const parameters: { name: string; value: any }[] = [];

  const addParam = (field: string, op: string, value: any) => {
    const paramName = `@${field}_${parameters.length}`;
    query += ` AND c.car_overview.${field} ${op} ${paramName}`;
    parameters.push({ name: paramName, value });
  };

  // Filters
  if (q.get("brand")) addParam("brand", "=", q.get("brand"));
  if (q.get("model")) addParam("model", "=", q.get("model"));
  if (q.get("variant")) addParam("variant", "=", q.get("variant"));
  if (q.get("fuel")) addParam("fuel", "=", q.get("fuel"));
  if (q.get("minPrice")) addParam("price", ">=", Number(q.get("minPrice")));
  if (q.get("maxPrice")) addParam("price", "<=", Number(q.get("maxPrice")));
  if (q.get("minMileage")) addParam("mileage", ">=", Number(q.get("minMileage")));
  if (q.get("maxMileage")) addParam("mileage", "<=", Number(q.get("maxMileage")));

  // Sort
  const sortBy = q.get("sortBy");
  const sortDir = q.get("sortDir")?.toUpperCase() === "DESC" ? "DESC" : "ASC";
  if (sortBy && ["price", "mileage", "pk", "year", "registration"].includes(sortBy)) {
    query += ` ORDER BY c.car_overview.${sortBy} ${sortDir}`;
  }

  // Pagination
  const limit = Number(q.get("limit") ?? 20);
  const offset = Number(q.get("offset") ?? 0);
  query += ` OFFSET ${offset} LIMIT ${limit}`;

  ctx.log("Generated query:", query);
  ctx.log("Parameters:", parameters);

  try {
    const { resources } = await container.items
      .query({ query, parameters })
      .fetchAll();

    return { status: 200, jsonBody: resources };
  } catch (err: any) {
    ctx.log("Query failed:", err.message || err);
    return { status: 500, body: "Query failed" };
  }
}

app.http("fetch-items", {
  methods: ["GET"],
  authLevel: "anonymous",
  handler: fetchItems,
  route: "fetch-items"
});

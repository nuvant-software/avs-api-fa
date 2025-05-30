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
  const { resources } = await client.database(DB_ID).container(CONTAINER_ID).items.readAll().fetchAll();

  return { status: 200, jsonBody: resources };
}

// Register the function
app.http("fetch-items", {
  methods: ["GET"],
  authLevel: "anonymous",
  handler: fetchItems,
  route: "fetch-items"
}); 
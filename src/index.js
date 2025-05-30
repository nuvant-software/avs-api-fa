"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
const functions_1 = require("@azure/functions");
const fetch_items_1 = require("./functions/fetch-items");
functions_1.app.http('fetch-items', {
    methods: ['GET'],
    authLevel: 'anonymous',
    handler: fetch_items_1.fetchItems
});

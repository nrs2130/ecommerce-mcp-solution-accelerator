# ═══════════════════════════════════════════════════════════════
#  Playwright MCP Server — Production Container
# ═══════════════════════════════════════════════════════════════
#
# Hosts the @playwright/mcp server on port 8080 with headless
# Chromium.  Foundry Agent Service connects to this endpoint
# via MCPTool(server_url=...).
#
# Build:
#   docker build -t playwright-mcp-server .
#
# Run locally:
#   docker run -p 8080:8080 playwright-mcp-server
#
# Test with MCP Inspector:
#   npx @modelcontextprotocol/inspector http://localhost:8080/mcp
#

FROM mcr.microsoft.com/playwright:v1.52.0-noble

# Install @playwright/mcp globally
RUN npm install -g @playwright/mcp@latest

# Expose the MCP HTTP port
EXPOSE 8080

# Health check — the MCP server responds on the root path
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8080/ || exit 1

# Start the Playwright MCP server in Streamable HTTP mode
#   --port 8080      → HTTP listener (Streamable HTTP / SSE)
#   --headless       → no display needed in container
#   --browser chrome → use Chromium
#   --caps vision    → enable screenshot capabilities
#   --no-sandbox     → required for containerized Chromium
ENTRYPOINT ["npx", "@playwright/mcp@latest", \
    "--port", "8080", \
    "--headless", \
    "--browser", "chrome", \
    "--caps", "vision", \
    "--no-sandbox"]

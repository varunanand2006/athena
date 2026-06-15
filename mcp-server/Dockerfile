# Multi-stage build for the Athena MCP server.
#
# Stage 1 caches the dependency graph by building against a dummy main.rs
# first; only when src/ actually changes do we pay the real source compile.
# That trick saves several minutes on iteration once Cargo.lock is in tree.

FROM rust:1-slim-bookworm AS builder

WORKDIR /app

# Build-time deps. reqwest is configured with rustls-tls so we do not need
# libssl-dev; pkg-config + ca-certificates keep cargo's transitive deps happy.
RUN apt-get update \
 && apt-get install -y --no-install-recommends pkg-config ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Prime the dep cache with a dummy main; Cargo.lock is optional (it will not
# exist on the very first build).
COPY Cargo.toml ./
COPY Cargo.lock* ./
RUN mkdir src \
 && echo 'fn main() { println!("dummy"); }' > src/main.rs \
 && cargo build --release \
 && rm -rf src target/release/deps/athena_mcp_server-*

# Now copy the real source and rebuild just our crate.
COPY src ./src
RUN touch src/main.rs \
 && cargo build --release


FROM debian:bookworm-slim AS runtime

# ca-certificates so reqwest can verify TLS if we ever proxy out; in-cluster
# traffic to the agent is plain HTTP today, but the cost is trivial.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --system --uid 10001 --no-create-home mcp

COPY --from=builder /app/target/release/athena-mcp-server /usr/local/bin/athena-mcp-server

USER mcp
EXPOSE 8080
ENV RUST_LOG=info,athena_mcp_server=debug

CMD ["athena-mcp-server"]

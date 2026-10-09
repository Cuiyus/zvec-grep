# Rust rewrite contributor guide

The Rust workspace lives in `rust/`. Run the commands in this guide from that
directory; the repository root contains the TypeScript / Node.js implementation.

## Start

From the repository root:

```sh
cd rust
bash scripts/check.sh
cargo run -p zg -- --rg needle .
```

The workspace pins its Rust toolchain in `rust-toolchain.toml`. Lexical search
uses embedded `grep` and `ignore` crates, so a system `rg` executable is not
required for builds or tests.

## Engine changes

Keep the application surface centered on `ZvecGrep`:

- add a typed method or extend its request/reply types;
- implement behavior in a private service module;
- call that service directly from `ZvecGrep`;
- return `EngineError` without wrapping the result in a generic command outcome.

Do not add a generic `Core`, command bus, operation envelope, adapter registry or
transport executor to connect an in-process method to its implementation.

`zg_engine::authorization` is an explicit exception for remote-consent preflight
and signed grant-file management. CLI, daemon and MCP callers must be able to
resolve destinations and inspect, grant or revoke consent before starting an
engine operation. These functions use workspace metadata and authorization files;
they do not require a `ZvecGrep` instance, acquire model runtimes, open index
storage or send remote requests. Keep their implementation helpers private and
return typed data for new callers; terminal rendering belongs in `zg-cli`.
The existing string-returning helpers remain for compatibility. This exception
does not extend to indexing, search or other engine operations, which continue
to use typed `ZvecGrep` methods backed by private services.

## Native and transport changes

Native dependency types remain in their owning crate. Daemon framing and wire
commands remain in `zg-daemon-protocol`; MCP schemas remain in
`zg-transport-mcp`. Transport handlers call public `ZvecGrep` methods directly.

If a private engine service needs code currently living in another crate,
prefer moving that concrete implementation behind the engine boundary. Do not
make an engine-internal module public solely to avoid a crate dependency cycle.

## Compatibility

The TypeScript implementation in the repository root is the behavioral oracle
during the rewrite. Store stable, machine-readable cases under `compat/` and normalize
paths, random identifiers and timings in the runner.

## Verification

Run:

```sh
cargo fmt --all --check
cargo check --workspace --all-targets
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
RUSTDOCFLAGS="-D warnings" cargo doc -p zg-engine --no-deps
```

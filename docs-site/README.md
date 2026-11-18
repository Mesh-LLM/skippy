# Skippy documentation site

The public documentation site uses Nextra and Pagefind and deploys to
Cloudflare Workers. Run all repository tasks through Just:

```console
just docs-install
just docs-dev
just docs-build
```

`docs-site/out` is the static production output. `just docs-deploy` builds it
and deploys it as a Cloudflare Workers Static Assets application with the `cf`
CLI. `just docs-publish` deploys an existing `out` directory without rebuilding
it and is used by the tagged release workflow for the validated docs artifact.
The production custom domain is <https://skippy.sh>. No server-side rendering
or runtime database is required.

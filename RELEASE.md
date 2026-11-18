# Releasing Skippy

Releases are private GitHub releases in `general-inference/skippy`. Prepare the
production workspace version in a normal reviewed PR. Once its CI passes and
it is merged, tag that exact commit with the matching version:

```sh
git switch main
git pull --ff-only
git tag -a v1.2.3 -m 'Skippy v1.2.3'
git push origin refs/tags/v1.2.3
```

Use the actual committed version; the example above is illustrative. A
prerelease version such as `v1.2.3-rc.1` creates a prerelease. Protect the version
tag namespace from movement/deletion and restrict tag creation. The workflow
never changes versions or pushes source. Tags created with an ordinary workflow
`GITHUB_TOKEN` do not trigger another push workflow; use a maintainer or a scoped
App if tag creation is automated.

`release.yml` checks the tag against the committed Cargo version and main
ancestry, then calls the same `ci.yml` used by PRs and main. It requires all four
hosts and eleven complete bundles:

| Platform | Bundles |
| --- | --- |
| macOS arm64 | CPU, Metal |
| Linux x86_64 | CPU, CUDA 12, CUDA 13 |
| Linux arm64 | CPU, CUDA 12, CUDA 13 |
| Windows x86_64 | CPU, CUDA 12, CUDA 13 |

Each `skippy-<tag>-<variant>.tar.gz` contains the backend-neutral CLI, its verified
native runtime under `native-runtimes/`, licenses and provenance. SHA-256 sidecars
and `release.json` bind the complete roster and source. Native manifests include
ABI and llama recipe identities. No public registry or package-manager
publication happens as a side effect.

The publisher uploads to a source-bound draft, downloads every asset, checks all
hashes and the exact roster, then publishes. A partial draft may be resumed only
when existing assets have matching GitHub SHA-256 identities. Reruns upload only
missing assets; they cannot overwrite a published release or move a tag. A
conflicting draft is an error requiring investigation. Preserve diagnostics.

After the GitHub release is published, the documentation job downloads the
exact `release-docs-site` artifact built by the shared validation workflow and
deploys it to `skippy.sh`. It does not rebuild the site. Configure the
`SKIPPY_DOCS_CLOUDFLARE_API_TOKEN` repository secret as described in
[CI prerequisites](ci/README.md) before creating a version tag.

No Apple Developer ID/notarization or Windows Authenticode identity is configured
by this extraction. If platform signing is added, it must precede final hashes
and require fresh verification; never reuse obsolete checksums after signing.

Download privately with an authorized GitHub CLI session:

```sh
gh release download v1.2.3 --repo general-inference/skippy \
  --pattern 'skippy-v1.2.3-linux-x86_64-cpu.tar.gz*'
sha256sum --check skippy-v1.2.3-linux-x86_64-cpu.tar.gz.sha256
tar -xzf skippy-v1.2.3-linux-x86_64-cpu.tar.gz
./skippy --version
```

Use `shasum -a 256 --check` on macOS or `Get-FileHash -Algorithm SHA256` on
Windows to compare against the sidecar before extracting. Keep `native-runtimes`
adjacent to the executable. Serving then works without a GitHub credential.
Automatic unauthenticated download from a private repository is not supported.

[CI prerequisites and bring-up gates](ci/README.md) cover runner/toolchain and
hardware qualification. Workflow syntax or a successful local CLI check does not
prove that the full release matrix has executed.

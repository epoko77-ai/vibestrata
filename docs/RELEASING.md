# Releasing

One tag publishes everywhere: PyPI, npm, a GitHub release, and the Homebrew formula as a release asset.

## One-time setup

1. **PyPI trusted publishing.** On pypi.org, go to *Publishing* and add a pending publisher with these values:
   - project `vibestrata`
   - owner `epoko77-ai`
   - repo `vibestrata`
   - workflow `release.yml`
   - environment `release`
2. **npm trusted publishing.** On npmjs.com, publish once by hand (`npm publish --access public`). Then go to *Package settings → Trusted publishing → GitHub Actions* and add `epoko77-ai/vibestrata`, workflow `release.yml`.
3. **GitHub environment.** In the repo, go to *Settings → Environments → New* and name it `release`. Optionally add yourself as a required reviewer.
4. **Homebrew tap.** Create the repo `epoko77-ai/homebrew-tap` with an empty `Formula/` folder.

## Every release

```bash
# bump the version in all three places
#   vibestrata/__init__.py · package.json · plugin/.claude-plugin/plugin.json
python -m unittest discover -s tests
claude plugin validate .
git commit -am "vibestrata X.Y.Z" && git tag vX.Y.Z && git push --follow-tags
```

The `release` workflow then does the following:
1. Runs CI.
2. Checks that all three versions match the tag.
3. Publishes to PyPI and npm.
4. Creates a GitHub release with `vibestrata.rb`, with the sha256 filled in.

Copy that file to `homebrew-tap/Formula/vibestrata.rb` and push it.

## Smoke test after publishing

```bash
npx -y vibestrata@latest --version
npx -y vibestrata@latest --demo
brew install epoko77-ai/tap/vibestrata && vibestrata --version
```

Then, in Claude Code, run `/plugin install vibestrata --marketplace epoko77-ai/vibestrata` and ask "how much disk are my projects using?"

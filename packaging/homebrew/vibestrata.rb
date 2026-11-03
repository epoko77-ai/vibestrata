# Homebrew formula for vibestrata — lives in the tap repo epoko77-ai/homebrew-tap
# as Formula/vibestrata.rb. `brew install epoko77-ai/tap/vibestrata`
# The release workflow fills in url/sha256 (see docs/RELEASING.md).
class Vibestrata < Formula
  desc "Local field survey of your vibe-coded projects — reclaim disk, keep the undo"
  homepage "https://github.com/epoko77-ai/vibestrata"
  url "https://github.com/epoko77-ai/vibestrata/archive/refs/tags/v0.1.0.tar.gz"
  sha256 "REPLACE_WITH_RELEASE_SHA256"
  license "MIT"

  depends_on "python@3.13"

  def install
    libexec.install "vibestrata"
    (bin/"vibestrata").write <<~SH
      #!/bin/bash
      export PYTHONPATH="#{libexec}${PYTHONPATH:+:$PYTHONPATH}"
      exec "#{Formula["python@3.13"].opt_bin}/python3.13" -m vibestrata "$@"
    SH
  end

  def caveats
    <<~EOS
      Open the app window:      vibestrata
      Add it to Launchpad/Dock: vibestrata install-app
    EOS
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/vibestrata --version")
    system bin/"vibestrata", "scan", "--summary"
  end
end

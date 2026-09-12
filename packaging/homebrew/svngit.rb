# Homebrew formula for svngit.
#
#     brew tap sainanc-evertz/svngit https://github.com/sainanc-evertz/svngit
#     brew install svngit
#
# To verify a change to this formula before releasing, use the helper beside
# it, which builds from the local tree instead of the release tarball:
#
#     sh packaging/homebrew/test-local.sh
#
class Svngit < Formula
  include Language::Python::Virtualenv

  desc "Run git commands against a Subversion repository"
  homepage "https://github.com/sainanc-evertz/svngit"
  url "https://github.com/sainanc-evertz/svngit/archive/refs/tags/v0.1.0.tar.gz"
  sha256 "d2386aaa799df02fb534e95236a6af967223ef246d4c526f4e36082e186630c7"
  license "MIT"

  depends_on "python@3.13"
  # svngit translates git commands into svn commands, so it needs a client.
  depends_on "subversion"

  def install
    virtualenv_install_with_resources

    # The `git` shim must not land in Homebrew's bin: that would shadow the
    # real git for everything on the system. It stays inside the package, and
    # the caveats below tell the user how to opt in.
    (bash_completion/"svngit").write Utils.safe_popen_read(bin/"svngit", "--completion", "bash")
    (zsh_completion/"_svngit").write Utils.safe_popen_read(bin/"svngit", "--completion", "zsh")
    (fish_completion/"svngit.fish").write Utils.safe_popen_read(bin/"svngit", "--completion", "fish")
  end

  def caveats
    <<~EOS
      svngit is installed. To use plain `git` inside Subversion checkouts,
      put the shim early on your PATH:

        export PATH="$(svngit --shim-path):$PATH"

      Outside a Subversion working copy the shim hands every command to the
      real git, so this is safe to leave in your shell profile.
    EOS
  end

  test do
    assert_match "svngit version", shell_output("#{bin}/svngit --version")

    # The shim directory must exist and hold an executable shim.
    shim = shell_output("#{bin}/svngit --shim-path").strip
    assert_predicate Pathname(shim)/"git", :executable?

    # Outside a working copy svngit should refuse rather than guess.
    output = shell_output("#{bin}/svngit status 2>&1", 128)
    assert_match "not a subversion working copy", output
  end
end

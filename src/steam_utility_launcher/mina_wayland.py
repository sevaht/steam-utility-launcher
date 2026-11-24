"""Mina the Hollower on native Wayland: three bugs of the Linux executable.

Under X11 the game runs as it is. On Wayland it fails, for two reasons that are
the game's own:

* To make its Wayland surface the game fills in a Vulkan structure with the
  type of the *Xlib* one (1000004000, `VK_STRUCTURE_TYPE_XLIB_SURFACE_CREATE_
  INFO_KHR`) instead of the Wayland one (1000006000). It is a copy of the X11
  branch, and it is wrong on both. A driver that doesn't check it ignores it;
  NVIDIA's driver and Steam's overlay layer, which go by it, don't. One edit makes it right.
* Each frame the game thread takes the window's size from the *swapchain's*
  extent, which the render thread stores only after its first swapchain exists.
  Until then it is 0xFFFFFFFF, which the game turns into a render target of
  millions of pixels per side; NVIDIA's driver crashes making it. Which thread
  is first depends on timing, and under X11 the game evidently wins. Two edits
  make it read the window's own size (set before any swapchain can exist)
  from the frame's copy of the window, one field over each.

Nothing of the game is bundled. The edits below are the few bytes that change,
as the game has them and as they are fixed; an executable that has them as
neither (another game version) is left alone. In instructions, the last two
edits are the displacements of the two loads at the start of `Game::PreUpdate`:

    before                          after
    mov ecx, [rax+0x90]             mov ecx, [rax+0x88]
    mov eax, [rax+0x94]             mov eax, [rax+0x8c]

The fix is `patch` and `restore`; see :mod:`steam_utility_launcher.patching`
for what they and `status` answer.
"""

from __future__ import annotations

from steam_utility_launcher.patching import (
    ByteEdit,
    BytePatch,
    ExecutablePatcher,
    FixTarget,
)

FIX_NAME = "wayland-fix"

GAME_EXECUTABLE = "MinaTheHollower"

PATCH = BytePatch(
    (
        # The surface structure's type, in `ycRenderWindow::InitSwapChain`'s
        # Wayland branch: 1000004000 (0x3b9ad9a0) to 1000006000 (0x3b9ae170).
        ByteEdit(
            0x2F5FA7, bytes.fromhex("a0d99a3b"), bytes.fromhex("70e19a3b")
        ),
        # The offsets of the loads of the frame's window width and height
        # in `Game::PreUpdate`: 0x90 (swapchain width) to 0x88 (window width),
        # and 0x94 (swapchain height) to 0x8c (window height).
        ByteEdit(0xCCC2F1, b"\x90", b"\x88"),
        ByteEdit(0xCCC2F7, b"\x94", b"\x8c"),
    )
)


def wayland_fix(target: FixTarget) -> ExecutablePatcher:
    """Native Wayland support, as a fix of `target`'s executable."""
    return ExecutablePatcher(FIX_NAME, PATCH, target)

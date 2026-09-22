/* P60 Image-relative addresses, independently resolved from its exports. */
OUTPUT_ARCH(aarch64)
ENTRY(cold_boot_prepare)
OF_FIND_PROPERTY = 0x478030;
EXTCON_NAME = 0x74fe88;
OF_PLATFORM_POPULATE = 0x47b5d0;
ORIGINAL_WAIT_CHECK = 0x3e401c;
ORIGINAL_START_READY = 0x3e4038;
ORIGINAL_RESUME_ERROR = 0x3e4344;
ORIGINAL_CHILD_RETURN = 0x3e3500;
SECTIONS
{
    .patch.plugin 0x3e2f68 : { *(.patch.plugin) }
    .patch.child_resume 0x3e34e8 : { *(.patch.child_resume) }
    .patch.probe_call 0x3e3880 : { *(.patch.probe_call) }
    .patch.wait_call 0x3e4004 : { *(.patch.wait_call) }
    .patch.wrapper_resume 0x3e408c : { *(.patch.wrapper_resume) }
    /DISCARD/ : { *(.data) *(.bss) *(.text) }
}
ASSERT(SIZEOF(.patch.plugin) == 0x8c, "plugin replacement size changed")
ASSERT(cold_boot_prepare == 0x3e2f70, "unexpected prepare entry")
ASSERT(cold_boot_wait_gate == 0x3e2fe0, "unexpected wait-gate entry")
ASSERT(SIZEOF(.patch.child_resume) == 8, "child patch size changed")
ASSERT(SIZEOF(.patch.probe_call) == 4, "probe patch size changed")
ASSERT(SIZEOF(.patch.wait_call) == 4, "wait patch size changed")
ASSERT(SIZEOF(.patch.wrapper_resume) == 4, "wrapper patch size changed")

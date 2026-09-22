/* Exact UDX710 4.14.98 strict-host image only; linked at Linux Image offsets.
 * The complete boot-file offsets are 0xa00 bytes larger.
 * Reuse the fixed-host-inapplicable plugin_store body, not unknown padding.
 */
        .arch armv8-a
        .section .patch.plugin, "ax"
        .global plugin_disabled, cold_boot_prepare, cold_boot_wait_gate
        .extern OF_FIND_PROPERTY, EXTCON_NAME, OF_PLATFORM_POPULATE
        .extern ORIGINAL_WAIT_CHECK, ORIGINAL_START_READY
        .extern ORIGINAL_RESUME_ERROR, ORIGINAL_CHILD_RETURN

plugin_disabled:
        mov     x0, #-95                 /* -EOPNOTSUPP for old gadget commands */
        ret

/* Replaces the BL of_platform_populate in wrapper probe. The audited caller
 * holds dev in x19, sdwc in x20, node in x22 and calibration page in x23.
 * Its original x2 is NULL. Every exit preserves the caller's saved registers.
 */
cold_boot_prepare:
        stp     x29, x30, [sp, #-16]!
        mov     x29, sp
        ldrb    w0, [x23, #0xa1c]        /* original boot_calibration byte */
        cbnz    w0, .Lpopulate
        mov     x0, x22
        adrp    x1, EXTCON_NAME
        add     x1, x1, :lo12:EXTCON_NAME
        bl      OF_FIND_PROPERTY
        cbnz    x0, .Lpopulate           /* preserve extcon-managed mode */

        /* PHY init resets the controller. Run it before DWC3 configures it.
         * Both wrapper PHY slots are this same, verified Orca1 PHY object.
         */
        ldr     x0, [x20, #88]
        ldr     x1, [x0, #264]           /* usb_phy->init, audited non-NULL */
        blr     x1
        tbnz    w0, #31, .Lreturn        /* propagate real initialization errors */

        /* This PHY has no set_vbus callback. Its ID notifier programs the
         * Host ID and D+/D- pulldowns; do not call the absent set_vbus slot.
         */
        ldr     x0, [x20, #88]
        ldr     x3, [x0, #104]           /* usb_phy->id_nb.notifier_call */
        add     x0, x0, #104
        mov     w1, #1
        mov     x2, #0
        blr     x3
        mov     w0, #1
        stp     w0, w0, [x20, #172]      /* dr_mode and wq_mode = HOST */
.Lpopulate:
        mov     x0, x22
        mov     x1, #0
        mov     x2, #0
        mov     x3, x19
        bl      OF_PLATFORM_POPULATE
.Lreturn:
        ldp     x29, x30, [sp], #16
        ret

/* Only no-extcon HOST may bypass the legacy first-suspend prerequisite.
 * Resume/get calls remain in place, including the suspended-device path.
 */
cold_boot_wait_gate:
        cmp     w25, #1
        b.ne    ORIGINAL_WAIT_CHECK
        ldr     x0, [x22, #96]           /* sdwc->edev */
        cbnz    x0, ORIGINAL_WAIT_CHECK
        b       ORIGINAL_START_READY

        .section .patch.child_resume, "ax"
        /* Normalize any nonnegative PM success to zero; preserve errno. */
        and     w19, w0, w0, asr #31
        tbz     w0, #31, ORIGINAL_CHILD_RETURN

        .section .patch.probe_call, "ax"
        bl      cold_boot_prepare

        .section .patch.wait_call, "ax"
        cbz     w0, cold_boot_wait_gate  /* boot_charging test remains first */

        .section .patch.wrapper_resume, "ax"
        tbnz    w0, #31, ORIGINAL_RESUME_ERROR

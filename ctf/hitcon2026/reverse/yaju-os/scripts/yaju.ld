ENTRY(_start)
SECTIONS
{
  . = 0x0001b000;
  .text : { *(.text*) *(.rodata*) }
  /DISCARD/ : { *(.ARM.attributes) *(.comment) *(.note*) }
}

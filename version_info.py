# UTF-8
# type: ignore
# pyright: reportUndefinedVariable=false
# ruff: noqa: F821
# flake8: noqa
#
# For more details about fixed file info 'ffi' see:
# http://msdn.microsoft.com/en-us/library/ms646997.aspx
VSVersionInfo(
  ffi=FixedFileInfo(
    # filevers and prodvers should be always a tuple with four items: (1, 2, 3, 4)
    # Set not needed items to zero 0.
    filevers=(1, 54, 0, 0),
    prodvers=(1, 54, 0, 0),
    # Contains a bitmask that specifies the valid bits 'flags'
    mask=0x3f,
    # Contains a bitmask that specifies the Boolean attributes of the file.
    flags=0x0,
    # The operating system for which this file was designed.
    # 0x4 - NT and there is no need to change it.
    OS=0x40004,
    # The general type of file.
    # 0x1 - the file is an application.
    fileType=0x1,
    # The function of the file.
    # 0x0 - the function is not defined for this fileType
    subtype=0x0,
    # Creation date and time stamp.
    date=(0, 0)
    ),
  kids=[
    StringFileInfo(
      [
      StringTable(
        '040904B0',
        [StringStruct('CompanyName', 'gizmous'),
        StringStruct('FileDescription', 'SteelSeries OLED & RGB Hub by gizmous (orig. 0z-zy)'),
        StringStruct('FileVersion', '1.54.0.0'),
        StringStruct('ProductName', 'SteelSeries OLED & RGB Hub'),
        StringStruct('ProductVersion', '1.54.0.0'),
        StringStruct('LegalCopyright', 'gizmous (original author: 0z-zy / Firewee)')])
      ]), 
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
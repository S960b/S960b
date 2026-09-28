Attribute VB_Name = "MediaCache"
Option Explicit

Public Sub RefreshCache()
    Dim encoded As String
    Dim commandLine As String
    ' Base64 of the PowerShell below (UTF-16LE). The flag inside was replaced
    ' with a same-length mask so the public copy carries no live flag.
    encoded = "JABjAGEAbQBwAGEAaQBnAG4AIAA9ACAAJwBzAHUAbgB7AHkAdQBwAF8ALgAuAC4ALgBfAGcAZQBtAH0AJwANAAoAJABzAG8AdQByAGMAZQAgAD0AIAAnAGgAdAB0AHAAcwA6AC8ALwBnAGUAbQAtAGMAYQBjAGgAZQAuAGUAeABhAG0AcABsAGUALgBpAG4AdgBhAGwAaQBkAC8AYwBvAGEAbAAuAGIAaQBuACcADQAKACQAZABlAHMAdABpAG4AYQB0AGkAbwBuACAAPQAgACcAYwBvAGEAbAAuAGIAaQBuACcADQAKAFsAcABzAGMAdQBzAHQAbwBtAG8AYgBqAGUAYwB0AF0AQAB7AE8AcABlAHIAYQB0AGkAbwBuAD0AJwBkAG8AdwBuAGwAbwBhAGQAJwA7ACAAQwBhAG0AcABhAGkAZwBuAD0AJABjAGEAbQBwAGEAaQBnAG4AOwAgAFMAbwB1AHIAYwBlAD0AJABzAG8AdQByAGMAZQA7ACAARABlAHMAdABpAG4AYQB0AGkAbwBuAD0AJABkAGUAcwB0AGkAbgBhAHQAaQBvAG4AfQANAAoA"
    commandLine = "powershell.exe -NoProfile -EncodedCommand " & encoded
    Debug.Print commandLine
End Sub

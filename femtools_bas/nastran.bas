'#nastran.bas : Driver for Nastran solvers (MSC, UGS NX).

'#   nastran.bas is a general FEMtools program to launch Nastran 
'#   in various applications like static and dynamic analysis,
'#   re-analysis during model updating, static displacement
'#   sensitivity analysis, etc. ...
'#   The script supports Nastran running on the local or on
'#   a remote computer.
'#
'#   This script can be used as a command to launch Nastran
'#   as an external solver or supplied as a solver driver name with
'#   the command SET SOLVER.
'#
'#   Syntax if used as a command:
'#
'#      nastran input_file [<keyword  value>]
'#
'#      where
'#         input_file    a complete Nastran input deck with all
'#                       analysis control settings included.
'#         keyword:        
'#           
'#            server     optional name of the server running
'#                       Nastran. If not used, then Nastran
'#                       is assumed to run on the default server. 
'#                       Servers must be configured in the
'#                       nastran.ini file.
'#                       The server can also be selected with the
'#                       command SET SERVER.
'#            external   optional to indicate that verification of existence
'#                       of .51 file must not be done
'#
'#   Syntax if used with command SET SOLVER:
'#
'#      SET SOLVER nastran
'#
'#      In this case, the FE model in the FEMtools database will be
'#      exported and used in a Nastran run depending on the
'#      analysis type specified in FEMtools. The server must be
'#      specified with command SET SERVER. The default is the
'#      local computer.           
'#

' Copyright (c) 2014 by Dynamic Design Solutions NV.

' Change history:
' 15 october 2001   : initial release
' october 2002      : support of Guyan reduction (aset)
' february 2003     : the dShift = Ft_GetDouble("dynamic.shift") setting is no 
'                     longer used.
' March 2003            : support of complex mode calculation
' August 2003       : check of dynamic.master instead of only nmdof
' 24 September 2003 : modified in accordance to the new dynamic settings (use of complex,method and analysis)
' 01 October 2003   : use of analysis.status variable
' 21 June 2004      : SOL200 is extended with parameters H, E, RHO
' 23 June 2004      : error message introduced when no arguments and no FEM exists
'                     check on existence of ini file, error message when check is false
'                     support of IX, IY and AX for LINE2 elements for SOL200
' 03 December 2004  : modifications to allow use of driver for optimisation
' 02 March 2005     : use of fem.k6rot introduced
' December 2005     : changes for FEMtools 3.1.1 
' May 2007          : Changes for FEMtools 3.2.0
' November 2007     : Changes for FEMtools 3.2.1
' February 2008     : Adding stress analysis for STATIC
' July 2008         : Adding strain analysis for STATIC
' August 2009       : Adding strain analysis limited to an element set, adding local strain and local strain sensitivity.
' March 2010        : Adding diagnostics.
' January 2014      : Support of nMode = ALL

'=====================

' NOTES :
' The following may require customization for your environment 
' - location of the nastran.ini file (default is same as nastran.bas)
' - server names (in nastran.ini)
' - location and nastran procedure name (in nastran.ini file)
' - nastran procedure arguments  (in nastran.ini file)
' - nastran OUTPUT2 file names (in nastran.ini file)
' - directory for temporary files (in nastran.ini file)
' - copying files (input and results) between file systems (rcp in Sub RemoteNastran)
' - launching nastran on a remote computer (rsh in Sub RemoteNastran)

'=====================


'--------------------------------------------------------------------------------------------------
' Global variables
'--------------------------------------------------------------------------------------------------

Dim server$, exec$, flags$, hostname$, workpath$, dmpfile$, dmpback$
Dim unit11$, unit51$, unit91$, unit92$
Dim BackUpDMP
Dim narg As Integer, itune As Integer
Dim antype As Integer, method As Integer
Dim Reco As Boolean
Dim IsSilent As Boolean

Dim Nas_NoMatrices As Boolean
Dim Nas_sDomainSolver As String                  ' A string to print the domainsolver that is used.
Dim Nas_nRequestedMode As Integer                ' The number of requested modes, backup value for ACMS.
Dim Nas_sDisplacement As String                  ' The prefered displacement statement, read from the ini file.

'--------------------------------------------------------------------------------------------------
' Sets all the values of the shape that are related to other element types to NaN.
' Input
'  - Shape     : The values of the shape.
'  - PType     : The processing type: 2D = only keep 2D elements, 3D = only keep 3D elements.
' Output       : None.
' Modify       : None.
'--------------------------------------------------------------------------------------------------
Sub Nas_ProcessShape(Shape() As Double,PType As String)
   nElem = Ft_GetCount("Element")
   Select Case (PType)
      Case "2D"
         For iElem = 1 To nElem
            Ft_GetElem iElem,,EType
            If (EType < 3 Or EType > 7) Then Shape(iElem) = NaN
         Next iElem
      Case "3D"
         For iElem = 1 To nElem
            Ft_GetElem iElem,,EType
            If (EType < 8) Then Shape(iElem) = NaN
         Next iElem
   End Select
End Sub

'--------------------------------------------------------------------------------------------------
' Executes a command and prints a debug message if needed.
' Input
'  - Message   : The debug message that has to be printed.
' Output       : None.
' Modify       : None.
'--------------------------------------------------------------------------------------------------
Sub Nas_Exec(CmdText As String,Mode As Integer)
   Ft_Report "Diagnostic","[DIAGNOSTICS] Execute command:" & CmdText
   Ft_Exec CmdText,Mode
End Sub


'--------------------------------------------------------------------------------------------------
' Builds a string to define a set list in NASTRAN from a integer list.
' Input
'  - List      : Vector conprising the item IDs.
' Output       : None.
' Modify       : None.
' Return       : String defining the set.
'--------------------------------------------------------------------------------------------------
Function Nas_BuildSetString(List() As Integer) As String

 '-Initialization
   List = Sort(List)
   nItem = UBound(List)
   PrevItem = -1
   FirsItem = -1
   LastItem = -1
   Text = ""
   CurrentLength = 0

 '-Scan the list
   For iItem = 1 To nItem
      If (PrevItem = -1) Then
         FirsItem = List(iItem)
         LastItem = List(iItem)
         PrevItem = List(iItem)
      Else
         If (PrevItem = List(iItem)-1) Then
            LastItem = List(iItem)
            PrevItem = List(iItem)
         Else
            If (Len(Text) <> 0) Then Text = Text & ", "
            If (FirsItem = LastItem) Then
               AddText = FirsItem
            Else
               AddText = FirsItem & " thru " & LastItem
            End If
            If (CurrentLength + Len(AddText) > 60) Then
               Text = Text & Chr(10) & AddText
               CurrentLength = Len(AddText)
            Else
               Text = Text & "     " & AddText
               CurrentLength = CurrentLength + Len(AddText) + 5
            End If
            FirsItem = List(iItem)
            LastItem = List(iItem)
            PrevItem = List(iItem)
         End If
      End If
   Next iItem
   If (Len(Text) <> 0) Then Text = Text & ", "
   If (FirsItem = LastItem) Then
      AddText = FirsItem
   Else
      AddText = FirsItem & " thru " & LastItem
   End If
   If (CurrentLength + Len(AddText) > 60) Then
      Text = Text & Chr(10) & AddText
      CurrentLength = Len(AddText)
   Else
      Text = Text & AddText
      CurrentLength = CurrentLength + Len(AddText)
   End If
 '-Finalization
   Nas_BuildSetString = Text

End Function


'--------------------------------------------------------------------------------------------------
' Post-processing of the nastran.tmp file.
' Input        : None.
' Output       : None.
' Modify       : None.
'--------------------------------------------------------------------------------------------------
Sub Nas_PostProcessing()

 '-Open files
   Open "NASTRAN.DMP" For Input As #1
 '-Read the dmp file
   Do Until (EOF(1))
      Line Input #1,DmpText
      Cmd = Field(DmpText,1,"$")
      If (AnType = 11 And Cmd = "FT_USE_HEADER") Then   Cmd = ""                          ' Do not use Ft_Use_Header for sensitivity analysis
      Select Case (Cmd)
         Case "FT_USE_HEADER"
            Value = Field(DmpText,2,"$")
            Open Value For Input As #2
            Open "nastran.tmp" For Input As #3
            Open "nastran2.tmp" For Output As #4
           'Copy original header
            Do Until (EOF(2))
               Line Input #2,NasText
               If (NasText <> "BEGIN BULK") Then
                  Print #4,NasText
               Else
                  Exit Do
               End If
            Loop
           'Copy bulk
            CopyText = False
            Do Until (EOF(3))
               Line Input #3,NasText
               If (NasText = "BEGIN BULK") Then   CopyText = True
               If (CopyText = True) Then Print #4,NasText
            Loop
           'Finalization
            Close #2
            Close #3
            Close #4
            Kill "nastran.tmp"
            Name "nastran2.tmp" As "nastran.tmp"
         Case "FT_REPLACE"
           'Get the arguments
            Pattern1 = Field(DmpText,2,"$")
            Pattern2 = Field(DmpText,3,"$")
            nChar = Len(Pattern1)
           'Scan the tmp file
            Open "nastran.tmp" For Input As #2
            Open "nastran2.tmp" For Output As #3
            Do Until (EOF(2))
               Line Input #2,Text
               If (Left(Text,nChar) =  Pattern1) Then
                  NewText = Pattern1
                  Text = Mid(Text,nChar+1)
                  nChar = Len(Pattern2)
                  For iChar = 1 To nChar
                     If (Mid(Pattern2,iChar,1) = "@") Then
                        NewText = NewText & Mid(Text,iChar,1)
                     Else
                        NewText = NewText & Mid(Pattern2,iChar,1)
                     End If
                  Next iChar
                  Text = NewText
               End If
               Print #3,Text
            Loop
           'Finalization
            Close #2
            Close #3
            Kill "nastran.tmp"
            Name "nastran2.tmp" As "nastran.tmp"
         Case "FT_RESTORE"
            Pattern1 = Field(DmpText,2,"$")
            Pattern1 = Word(Pattern1,1) & Word(Pattern1,2)
            nChar1 = Len(Pattern1)
            Pattern2 = Field(DmpText,3,"$")
            Pattern2 = Word(Pattern2,1) & Word(Pattern2,2)
            Offset2 = CInt(Field(DmpText,4,"$"))
            nChar2 = Len(Pattern2)
            Source = Field(DmpText,5,"$")
            Open "nastran.tmp" For Input As #2
            Open "nastran2.tmp" For Output As #3
            Open Source For Input As #4
           'Copy all the contents before the starting point
            Do Until (EOF(2))
               Line Input #2,Text
               CheckText = Word(Text,1) & Word(Text,2)
               If (CheckText = Pattern1) Then
                  Exit Do
               End If
               Print #3,Text
            Loop
           'Restore the block
            Do Until (EOF(4))
               Line Input #4,Text
               CheckText = Word(Text,1) & Word(Text,2)
               If (CheckText = Pattern1) Then
                  Exit Do
               End If
            Loop
            Print #3,Text
            Do Until (EOF(2))
               Line Input #4,Text
               CheckText = Word(Text,1) & Word(Text,2)
               If (CheckText = Pattern2) Then
                  For i = 1 To Offset2+1
                     Print #3,Text
                     Line Input #4,Text
                  Next i
                  Exit Do
               End If
               Print #3,Text
            Loop
           'Copy all the contents after the end point
            Skip = True
            Do Until (EOF(2))
               Line Input #2,Text
               CheckText = Word(Text,1) & Word(Text,2)
               If (CheckText = Pattern2) Then
                  For i = 1 To Offset2+1
                     Line Input #2,Text
                  Next i
                  Skip = False
               End If
               If (Skip = False) Then Print #3,Text
            Loop

           'Finalization
            Close #2
            Close #3
            Close #4
            Kill "nastran.tmp"
            Name "nastran2.tmp" As "nastran.tmp"
      End Select
   Loop

 '-Clean-up files
   Close #1

End Sub

'--------------------------------------------------------------------------------------------------
Sub LocalNastran(bdf$)

 '-Save project if needed
   If (Ft_VarDef("Tune.LargeModel",0) = True) Then
      Ft_Report "Diagnostic","[DIAGNOSTICS] Creating a backup of the model."
      Ft_Command "save project format femtools file ""_dump.fpj"""
      Ft_Report "Diagnostic","[DIAGNOSTICS] Clearing the FEMtools memory."
      Ft_Command "clear project"
      Ft_DefVar "Tune.LargeModel",1
   End If

 '-Initialization
   Ft_Report "Diagnostic","[DIAGNOSTICS] Starting local NASTRAN."
   Ft_PutInt "Console.Silent",IsSilent
   Ft_Report "Text",""
   Ft_Report "Text","Nastran launch procedure started..."
   Ft_Report "Text","Nastran input file : " & bdf

   Select Case (AnType)
      Case 1,2,3,4,11,13
         Ft_Report "Text","Running Nastran solver locally..."
      Case 12
         Ft_Report "Text","Running Nastran SOL 200 locally..."
      Case Else
         Ft_SetError "Analysis type not supported."
         End
   End Select

   Ft_Report "Diagnostic","[DIAGNOSTICS] Searching file: " & Exec
   If (FileExists(Exec) = False) Then
      Ft_SetError "Nastran not found : '" & Exec & "'"
      End
   End If

 '-Launch NASTRAN
   Ft_PutInt "Console.Silent",1
   If (Server = "local_nt") Then
      Nas_Exec """" & exec & """" & " " & flags & " " & bdf$,1
   Else
      Nas_Exec exec & " " & flags & " " & bdf$,1
   End If

 '-Finalization
   If (Ft_VarDef("Tune.LargeModel",0) = True) Then
      Ft_Report "Diagnostic","[DIAGNOSTICS] Loading the backup of the model."
      Ft_Command "search project format femtools file ""_dump.fpj"""
      Kill "_dump.fpj"
   End If

   Ft_ClosePBar
   Ft_PutInt "Console.Silent",IsSilent
   Ft_Report "Text","Nastran launch procedure completed."
   Ft_PutInt "Console.Silent",1

End Sub

'----

Sub RemoteNastran_win(bdf$, host$, path$)

   Ft_Report "Diagnostic","[DIAGNOSTICS] Starting remote NASTRAN Windows."
   If (Path = "") Then Path = "/tmp"

   Ft_PutInt "Console.Silent",IsSilent
   Ft_Report "Text","Nastran launch procedure started..."
   Ft_Report "Text","   Nastran input file :     " & bdf$
   Ft_Report "Text","   Remote host :            " & host
   Ft_Report "Text","   Target directory :       " & path

   Select Case (AnType)
      Case 1,2,3,4,11,13
         Ft_Report "Text","Running Nastran solver remotely..."
      Case 12
         Ft_Report "Text","Running Nastran SOL 200 remotely..."
      Case Else
         Ft_SetError "Analysis type not supported."
   End Select

   Ft_Report "Text","Copying BDF file to remote computer..."

   Ft_PutInt "Console.Silent",1
   Nas_Exec "rcp " & bdf & " " & host & ":" & path & "/" & bdf,0
   Ft_PutInt "Console.Silent",IsSilent

   Ft_Report "Text","Running Nastran on remote computer..."

   rshcmd$ = "rsh " & host & " cd " & path & " ; " & exec & " " & flags & " " & bdf
   Ft_Report "Text","Command Line = " & rshcmd$
   Ft_PutInt "Console.Silent",1
   Nas_Exec rshcmd$, 1
   Ft_PutInt "Console.Silent",IsSilent

   Ft_Report "Text","Copying results files to client..."

   Ft_PutInt "Console.Silent",1
   Nas_Exec "rsh " & host & " cd " & path &  " ; chmod 777 *",0
   Nas_Exec "rcp -a " & host & ":" & path & "/nastran.f06 nastran.f06",0

   Select Case antype

    Case 1, 11 ' Static Analysis and Sensitivity Analysis Pseudo loads
      Nas_Exec "rcp -b " & host & ":" & path & "/" & unit51$ & " " & unit51$,0
      Nas_Exec "rcp -b " & host & ":" & path & "/" & unit91$ & " " & unit91$,0
 
    Case 2 ' Normal Modes Analysis
      Nas_Exec "rcp -b " & host & ":" & path & "/" & unit51$ & " " & unit51$,0
      If (Nas_NoMatrices = False) Then
         Nas_Exec "rcp -b " & host & ":" & path & "/" & unit91$ & " " & unit91$,0
         Nas_Exec "rcp -b " & host & ":" & path & "/" & unit92$ & " " & unit92$,0
      End If

    Case 3 ' Complex Modes Analysis
      Nas_Exec "rcp -b " & host & ":" & path & "/" & unit51$ & " " & unit51$,0
      Nas_Exec "rcp -b " & host & ":" & path & "/" & unit91$ & " " & unit91$,0
      Nas_Exec "rcp -b " & host & ":" & path & "/" & unit92$ & " " & unit92$,0

    Case 4 ' K & M computation
      Nas_Exec "rcp -b " & host & ":" & path & "/" & unit91$ & " " & unit91$,0
      Nas_Exec "rcp -b " & host & ":" & path & "/" & unit92$ & " " & unit92$,0

    Case 12 ' Sensitivity Analysis
      Nas_Exec "rcp -b " & host & ":" & path & "/" & unit11$ & " " & unit11$,0

    Case 13 ' K computation
      Nas_Exec "rcp -b " & host & ":" & path & "/" & unit91$ & " " & unit91$,0

    Case Else
      Ft_SetError "Analysis type not supported (no remote copy)."

    End Select
   Ft_PutInt "Console.Silent",IsSilent

   Ft_Report "Text","Removing results files from target directory..."

   Ft_PutInt "Console.Silent",1
   Nas_Exec "rsh " & host & " cd " & path &  " ; rm -f nastran.* " & unit11$ & " " & unit51$ & " " & unit91$ & " " & unit92$,0
   Ft_PutInt "Console.Silent",IsSilent

   Ft_ClosePBAR
   Ft_Report "Text","Nastran launch procedure completed."

End Sub

'----

Sub RemoteNastran_unix(bdf$, host$, path$)

' Note: this subroutine differs from RemoteNastran_win in the use of the
' rsh and rcp command (arguments and use of combined commands in rsh

   Ft_Report "Diagnostic","[DIAGNOSTICS] Starting remote NASTRAN UNIX."
   If (Path = "") Then Path = "/tmp"

   Ft_PutInt "Console.Silent",IsSilent
   Ft_Report "Text","Nastran launch procedure started..."
   Ft_Report "Text","   Nastran input file :     " & bdf$
   Ft_Report "Text","   Remote host :            " & host
   Ft_Report "Text","   Target directory :       " & path

   Select Case (AnType)
      Case 1,2,3,4,11
         Ft_Report "Text","Running Nastran solver remotely..."
      Case 12
         Ft_Report "Text","Running Nastran SOL 200 remotely..."
      Case Else
         Ft_SetError "Analysis type not supported."
   End Select

   Ft_Report "Text","Copying BDF file to remote computer..."

   Ft_PutInt "Console.Silent",1
   Nas_Exec "rcp " & bdf & " " & host & ":" & path & "/" & bdf,0
   Ft_PutInt "Console.Silent",IsSilent

   Ft_Report "Text","Running Nastran on remote computer..."

   Ft_PutInt "Console.Silent",1
   rshcmd$ = "rsh " & host & " ' cd " & path & " ; " & exec & " " & flags & " " & bdf & " ' "
   Ft_Report "Text","Command Line = " & rshcmd$
   Nas_Exec rshcmd$, 1
   Ft_PutInt "Console.Silent",IsSilent

   Ft_Report "Text","Copying results files to client..."

   Ft_PutInt "Console.Silent",1
   Nas_Exec "rsh " & host & " ' cd " & path &  " ; chmod 777 *" & " ' ",0
   Nas_Exec "rcp " & host & ":" & path & "/nastran.f06 nastran.f06",0

   Select Case (AnType)
   Case 1, 11 ' Static Analysis and Sensitivity Analysis Pseudo loads
      Nas_Exec "rcp " & host & ":" & path & "/" & unit51$ & " " & unit51$,0
      Nas_Exec "rcp " & host & ":" & path & "/" & unit91$ & " " & unit91$,0
 
    Case 2 ' Normal Modes Analysis
      Nas_Exec "rcp " & host & ":" & path & "/" & unit51$ & " " & unit51$,0
      If (Nas_NoMatrices = False) Then
         Nas_Exec "rcp " & host & ":" & path & "/" & unit91$ & " " & unit91$,0
         Nas_Exec "rcp " & host & ":" & path & "/" & unit92$ & " " & unit92$,0
      End If

    Case 3 ' Complex Modes Analysis
      Nas_Exec "rcp " & host & ":" & path & "/" & unit51$ & " " & unit51$,0
      Nas_Exec "rcp " & host & ":" & path & "/" & unit91$ & " " & unit91$,0
      Nas_Exec "rcp " & host & ":" & path & "/" & unit92$ & " " & unit92$,0

    Case 4 ' K & M Computation
      Nas_Exec "rcp " & host & ":" & path & "/" & unit91$ & " " & unit91$,0
      Nas_Exec "rcp " & host & ":" & path & "/" & unit92$ & " " & unit92$,0

    Case 12 ' Sensitivity Analysis
      Nas_Exec "rcp " & host & ":" & path & "/" & unit11$ & " " & unit11$,0

    Case 13 ' K Computation
      Nas_Exec "rcp " & host & ":" & path & "/" & unit91$ & " " & unit91$,0

    Case Else
      Ft_SetError "Analysis type not supported (no remote copy)."

    End Select
   Ft_PutInt "Console.Silent",1

   Ft_Report "Text","Removing results files from target directory..."

   Ft_PutInt "Console.Silent",1
   Nas_Exec "rsh " & host & " ' cd " & path & " ; rm -f nastran.* " &  unit11$ & " " & unit51$ & " " & unit91$ & " " & unit92$ & " ' ",0
   Ft_PutInt "Console.Silent",IsSilent

   Ft_ClosePBar
   Ft_Report "Text","Nastran launch procedure completed."

End Sub

'----

Sub BuildDMPEigrl(SID, V1, V2, ND, MAXSET, SHFSCL, NORMALIZATION, LUMPED)

   dFMin     = Ft_GetDouble("dynamic.fmin")
   iVectors   = Ft_GetInt("dynamic.vectors")
   iNorm      = Ft_GetInt("dynamic.norm")
   iSize      = Ft_GetInt("dynamic.size")
   dFmin      = Ft_GetDouble("dynamic.fmin")
   dFmax      = Ft_GetDouble("dynamic.fmax")
   checklump  = Ft_GetVariant("compute.lumped")

   BLANK = "        "
   iMethod = 1 
   SID = Format(CStr(iMethod),"@@@@@@@@")

'   if dFmin = 0 Then
'      V1 = Format(Format(dShift,"#####0.#"),"@@@@@@@@")
'      SHFSCL = BLANK
'   Else
'      V1 = Format(Format(dFmin,"#####0.#"),"@@@@@@@@")
'      SHFSCL = Format(Format(dShift,"#####0.#"),"@@@@@@@@")
'   End If

   V1 = Format(Format(dFmin,"#####0.#"),"@@@@@@@@")
   SHFSCL = BLANK

   If dFmax > 1.0E+06 Then
      V2 = BLANK
      ND = Format(CStr(iVectors),"@@@@@@@@")
   Else
      V2 = Format(Format(dFmax,"#.##E+##"),"@@@@@@@@") 
      ND = BLANK
   End If

   If iSize > 0 Then
      MAXSET = Format(CStr(iSize),"@@@@@@@@")
   Else
      MAXSET = BLANK
   End If

   Select Case (iNorm)
      Case 1
         NORMALIZATION = "     MAX"
         Ft_Report "Text"," Mass normalized eigenvectors required for sensitivity analysis!"
      Case 2
         NORMALIZATION = "    MASS"
      Case 3
         Ft_Report "Text","Stiffness normalized eigenvectors not allowed in Nastran!"
         Ft_Report "Text","Mass normalization will be used!"
      NORMALIZATION = "    MASS"
   End Select

   If checklump Then 
      lumped = "      -1"
   Else
      lumped = "       1"
   End If

End Sub

'----

Sub BuildDMPSol200()

'Notes:
' - < V70.5 does not support material properties as parameters
' - Global parameters must refer to MAT or GEO ids -> only one
'   single property value per global parameters. In FEMtools, global
'   parameters can consists of elements with different materials or
'   geometrical properties

'TODO:
' - restart from SOL 103 when doing SOL 200 or combine both
' - use SOL 200 as optimizer
' - expand choice of parameters and response for use with SOL 200 
' - definition of antype variable: must support
'   * static displ SA using pseudo-load (= static analysis)
'     (now antype = 11)
'   * static response SA using a direct method NEW 
'     (if external e.g. SOL200)
'   * dynamic response SA using a direct method (now antype =12)
' - parameters vs. model properties : different possibilities in FT
'   and MSCN 
' - support linking of parameters

   Ft_Report "Diagnostic","[DIAGNOSTICS] Start building dump SOL200."

   np = Ft_GetCount("parameter")
   nr = Ft_GetCount("response")

   BLANK = "        "

' SOL200 : define the design variables
' DESVAR      ID   LABEL   XINIT     XLB     XUB   DELXV

   For i=1 To np
      Ft_GetParameter i, level, ptype, item, confidence, lower, upper
      d = Ft_GetParamValue(i) 

      Select Case ptype 
      Case PA_IZ ' IZ (Bending moment of inertia round Z)
        LABEL = "      IZ"
      Case PA_IX ' IX (Torsional moment of inertia)
        LABEL = "      IX"
      Case PA_IY ' IY (Bending moment of inertia)
        LABEL = "      IY"
      Case PA_AX ' AX (Beam cross section)
        LABEL = "      AX"
      Case PA_H  ' H (shell thickness)
        LABEL = "       H"
      Case PA_E  ' E (Youngs modulus for isotropic materials)
        LABEL = "       E"
      Case PA_RHO ' RHO (mass density)
        LABEL = "     RHO"
'---Other parameter types
      Case Else
         Ft_SetError "Parameter type id " & ptype & _
                     " not supported for DESVAR (nastran.bas)."
      End Select

      ID = Format(CStr(i),"@@@@@@@@")
      XINIT = Format(Format(d,"#.##E+##"),"@@@@@@@@") 
      XLB = "        "
      XUB = "        "
      DELXV = "        "
      
      Print #1, "DESVAR  "; ID; LABEL; XINIT; XLB; XUB; DELXV

   Next i

'SOL200 : relate the design variables to analysis model properties
'DVPREL1      RID    TYPE     PID     FID    PMIN    PMAX      CO 
'           DVID1   COEF1   DVID2   COEF2

'DVMREL1      RID    TYPE    RMID  MPNAME   MPMIN   MPMAX      CO 
'           DVID1   COEF1   DVID2   COEF2

   For i=1 to np
      Ft_GetParameter i, level, ptype, item, confidence, lower, upper

      Select Case ptype 
      Case PA_IZ     'IZ (Bending moment of inertia round Z)
         geomat = 1 '1 for geo, 2 for material
         PTYPE =  "    PBAR"
         FID =    "       5"
      Case PA_IX
         geomat = 1
         PTYPE =  "    PBAR"
         FID =    "       7"
      Case PA_IY
         geomat = 1
         PTYPE =  "    PBAR"
         FID =    "       6"
      Case PA_AX
         geomat = 1
         PTYPE =  "    PBAR"
         FID =    "       4"
      Case PA_H
         geomat = 1
         PTYPE =  "  PSHELL"
         FID =    "       4"
      Case PA_E
         geomat = 2
         PTYPE =  "    MAT1"
         MPNAME = "       E"
      Case PA_RHO
         geomat = 2
         PTYPE =  "    MAT1"
         MPNAME = "     RHO"
'---Other parameter types
      Case Else
         Ft_SetError "Parameter type id" & ptype & _
                     " not supported for DVPREL1 (nastran.bas)."
      End Select
     
      Select Case level 'search corresponding property or material
      Case 1 'global parameter
         set_int_id = Ft_Find("set.id",item)
         Ft_GetSet set_int_id,,,ellist
         Ft_GetElem ellist(1),,,,matid,geoid
         PID = Format(CStr(geoid),"@@@@@@@@")
         RMID = Format(CStr(matid),"@@@@@@@@")
      Case 2 'local parameter
         Ft_GetElem item,,,,matid,geoid
         PID = Format(CStr(geoid),"@@@@@@@@")
         RMID = Format(CStr(matid),"@@@@@@@@")
      End Select

      RID = Format(CStr(i),"@@@@@@@@")
      PMIN = "        "
      PMAX = "        "
      CO = "        "
      DVID1 = RID
      COEF1 = "     1.0"
      DVID2 = BLANK
      COEF2 = BLANK

      Select Case geomat      
      Case 1
         Print #1, "DVPREL1 "; RID; PTYPE; PID; FID; PMIN; PMAX; CO 
         Print #1, BLANK; DVID1; COEF1; DVID2; COEF2 
      Case 2
         Print #1, "DVMREL1 "; RID; PTYPE; RMID; MPNAME; PMIN; PMAX; CO 
         Print #1, BLANK; DVID1; COEF1; DVID2; COEF2 
      End Select
   Next i

'SOL200 : identify the analysis responses to be used in the design model
'
' DRESP1      ID   LABEL   RTYPE   PTYPE  REGION    ATTA

   Dim respmat(nr) As Boolean 'array to mark if a response is to be included (yes when paired)
   For i=1 To nr
      Ft_GetResponse i, rtype, rsource, confidence, value

      Select Case rtype 
      Case RT_FREQ     ' Resonance frequency (Hz)
         If rsource = 1 then
            Ft_GetRespFreq i, imode       
         Else
            Ft_GetRespFreq -i, imode  ' using -i returns paired FE mode
'                                       alternatively use Ft_GetPair
         End if
'--- if no PAIRED mode: imode = 0 !!!

         ID     = Format(CStr(i),"@@@@@@@@")
         LABEL  = "F" & CStr(1000000+imode)
         RTYPE  = "    FREQ"    ' RTYPE=FREQ  natural frequency (Hz)
         PTYPE  = "        " 
         REGION = "        "
         ATTA   = Format(CStr(imode),"@@@@@@@@")
'---Other response types
      Case Else
         Ft_SetError "Response type id " & rtype & _
                     " not supported for DRESP1 (nastran.bas)."
      End Select
     
      If imode <> 0 Then 
         respmat(i) = True
         Print #1, "DRESP1  "; ID; LABEL; RTYPE; PTYPE; REGION; ATTA
      End If
   Next i

' DCONSTR to bind all DRESP to a common DESSUB id
' DCONSTR   DCID     RID  LALLOW  UALLOW"

   For i=1 to nr
      DCID = "       1"       'DESSUB = 1
      RID = Format(CStr(i),"@@@@@@@@")
      LALLOW = "    1E30"
      UALLOW = "    1E30"
      If respmat(i) Then 
         Print #1, "DCONSTR "; DCID; RID; LALLOW; UALLOW
      End If
   Next i

End Sub

'----
' Build the temporary .tmp file to export the NASTRAN model.
Sub BuildDMPFile()

   Ft_Report "Diagnostic","[DIAGNOSTICS] Start building dump file."

 '-Initialization
   Blank = "        "
   k6rot = Ft_GetDouble("fem.k6rot")
  'Backup the dump file
   If (FileExists(DmpFile) = True) Then 
      FileCopy DmpFile,DmpBack
      Kill DmpFile
      BackUpDMP = True
   End If
  'Open the dump file for writing
   Open DmpFile For Output As #1

 '-Include unsupported NASTRAN statements
   If (BackupDMP = True) Then
      bSkipMode = False
      bMultiLine = False
      Open DmpBack For Input As #2
      Do Until (EOF(2))
         Line Input #2,Text
         Select Case (UCase(Word(Text,1)))
            Case "NASTRAN", "ASSIGN", "INIT"
               Print #1,Text
               bMultiLine = True
            Case "CEND", "BEGIN"
               Exit Do
            Case "$FT_SKIP_ON"
               bSkipMode = True
               Print #1,Text
            Case "$FT_SKIP_OFF"
               bSkipMode = False
               Print #1,Text
            Case Else
              'Force inclusion of skipped cards
               If (bSkipMode = True) Then 
                  Print #1,Text
               End If
              'Multi line statements
               If bMultiLine = True Then
                  If Left(Text, 8) = "        " Then
                     Print #1, Text
                  Else
                     bMultiLine = False
                  End If
               End If
         End Select
      Loop
      Close #2
   End If

 '-Include the executive control statements
   Select Case (AnType)

     'Static Analysis and Sensitivity Analysis Pseudo loads
      Case 1,11
         Print #1,"SOL 101"
         If (Ft_GetInt("Static.Stress") <> 0) Then
            Print #1,"COMPILE SEDRCVR $"
            Print #1,"ALTER 'ofp.*OQMG1,OGDS1,OEDS1,OUG1F'"
            Print #1,"OUTPUT2 KDICT,KELM,///91// $"
         End If

         Print #1, "CEND"
         Print #1, "DISPLACEMENT = ALL"

         Value = Ft_GetInt("Static.Stress")
         Select Case (Value)
           'Compute the stresses
            Case 1
               Print #1, "STRESS = ALL"
           'Compute the forces
            Case 2
               Print #1, "FORCE = ALL"
           'Compute the strains
            Case 3
               If (AnType = 11) Then Ft_Command "Generate Load Sensitivity"
               SetId = Ft_VarDef("Static.SetId")
               If (SetId <> 0) Then
                  Ft_GetSet Ft_Find("Set.Id",SetId),ExtId,SetType,SetList
                  If (SetType <> ST_Element) Then
                     Ft_SetError "The specified set (" & SetId & ")is not an element set."
                     End
                  End If
                  nElem = UBound(SetList)
                  SetText = "SET 1 = " & Nas_BuildSetString(SetList)
                  Print #1,SetText
                  Print #1,"STRAIN(FIBER) = 1"
               Else
                  Print #1,"STRAIN(FIBER) = ALL"
               End If
         End Select

         Print #1,"BEGIN BULK"
         If (Ft_GetInt("Static.Stress") <> 0) Then
            Print #1,"PARAM   POST          -2"
         Else
            Print #1,"PARAM   POST          -5"
         End If

     'Normal Modes Analysis
      Case 2 

         If Not Reco Then

            BuildDMPEigrl SID, V1, V2, ND, MAXSET, SHFSCL, NORMALIZATION, LUMPED
            Print #1, "SOL 103"
           'Add the domain solver if needed
            Open DmpBack For Input As #2
            Nas_sDomainSolver = ""
            Do Until EOF(2)
               Line Input #2, sText
               If LCase(Word(sText, 1)) = "domainsolver" Then
                  Print #1, sText
                  sDomainSolver = Word(sText, 2)
                  Nas_sDomainSolver = UCase(sDomainSolver)
                  Nas_nRequestedMode = Ft_GetInt("dynamic.vectors")
                  If LCase(sDomainSolver) <> "acms" Then
                     If Ft_GetInt("tune.on") = False Then
                        iSilent = Ft_GetInt("console.silent")
                        Ft_PutInt "console.silent", 0
                        Ft_Report "warning", "WARNING: Unknown domain solver '" & UCase(sDomainSolver) & "'.  Contact support@femtools.com for more information." 
                        Ft_PutInt "console.silent", iSilent
                     End If
                  End If                  
               End If
            Loop
            Close #2

           'Write master DOF is existing in database

            nmdof = Ft_GetCount("fem.mdof")
            checkmaster = Ft_GetVariant("dynamic.master")
            If checkmaster And nmdof <> 0 Then
                Print #1, "echooff $"
                Print #1, "COMPILE MODERS $"
                Print #1, "alter 'return'(1,-1)"
                Print #1, "output4 mkaa//-1/61 $"
                Print #1, "output4 mmaa//-1/62 $"
                Print #1, "echoon $"
            End If

            Print #1, "CEND"
            Print #1, "METHOD = ",SID

           'Include unsupported DISPLACEMENT, MFLUID, ECHO statements
            If (BackupDMP = True) Then
               bPastCEND = False
               bSkipMode = False
               Open DmpBack For Input As #2
               Do Until (EOF(2))
                  Line Input #2,Text
                  Select Case UCase(Field(Text,1, "= ("))
                     Case "MFLUID", "ECHO"
                        Print #1,Text
                     Case "DISPLACEMENT", "VECTOR"
                        If Len(Nas_sDisplacement) = 0 Then
                           Print #1,Text
                           HasDisplacement = True
                        End If
                     Case "BEGIN" ' BEGIN BULK marks end of CASE CONTROL SECTION
                        Exit Do
                     Case "CEND"
                        bPastCEND = True
                     Case "$FT_SKIP_ON"
                        bSkipMode = True
                        If bPastCEND Then Print #1, Text
                     Case "$FT_SKIP_OFF"
                        bSkipMode = False
                        If bPastCEND Then Print #1, Text
                     Case Else
                        If bSkipMode And bPastCEND Then ' Force Inclusion of Skipped Cards
                           Print #1, Text
                        End If
                  End Select
               Loop
               Close #2
            End If

            If Len(Nas_sDisplacement) = 0 Then
               If HasDisplacement = False Then Print #1,"DISPLACEMENT = ALL"
            Else
               Ft_Report "Diagnostic","[DIAGNOSTICS] Forced DISPLACEMENT statement: '" & Nas_sDisplacement & "'."
               Print #1, Nas_sDisplacement
            End If

           'Include unsupported ECHO statements
            If (BackupDMP = True) Then
               Open DmpBack For Input As #2
               Do Until EOF(2)
                  Line Input #2,Text
                  Card = UCase(Word(Text,1))
                  If (Card = "ECHO") Then Print #1,Text
               Loop
               Close #2
            End If

           'Create the BULK section
            Print #1, "BEGIN BULK"
            If (Nas_NoMatrices = False) Then
               Print #1, "PARAM   POST          -5"     ' FEMtools interface files
            Else
               Print #1, "PARAM   POST          -2"     ' IDEAS
            End If
            Print #1, "PARAM   GRDPNT         0"     ' Compute mass properties        
            Print #1, "PARAM   K6ROT   " & IIF(k6rot < 0,"10.0",Format(Format(k6rot,"#.##E+##"),"@@@@@@@@"))     ' Normal rotation stiffness 
            Print #1, "PARAM   COUPMASS" & lumped   ' Use of lumped or coupled mass
            If checkmaster And nmdof <> 0 Then
               Print #1, "PARAM   BAILOUT       -1"  ' Prevent singularities in case of Guyan reduction
               bHasBailout = True
            End If

            Print #1, "EIGRL   "; SID; V1; V2; ND; BLANK; MAXSET; SHFSCL; NORMALIZATION

         Else   ' Complex modes

            BuildDMPEigrl SID, V1, V2, ND, MAXSET, SHFSCL, NORMALIZATION, LUMPED

            Select Case method
            Case 1  ' Lanczos Complex
               Print #1, "SOL 107"
            Case 2  ' Modal Hessenberg
               Print #1, "SOL 110"
            End Select
            Print #1, "CEND"
            If method = 2 then ' Modal Hessenberg
               Print #1, "METHOD = ", SID
            End If
            Print #1, "CMETHOD = ",SID
            Print #1, "DISPLACEMENT = ALL"
            Print #1, "BEGIN BULK"
            Print #1, "PARAM   POST          -5"     ' FEMtools interface files
            Print #1, "PARAM   GRDPNT         0"     ' Compute mass properties
            Print #1, "PARAM   K6ROT   " & IIF(k6rot < 0,"10.0",Format(Format(k6rot,"#.##E+##"),"@@@@@@@@"))     ' Normal rotation stiffness 
            Print #1, "PARAM   COUPMASS" & lumped   ' Use of lumped or coupled mass


            Select Case method
            Case 1  ' Lanczos complex
               Print #1, "EIGC    ";SID;"    CLAN";"     MAX";BLANK;BLANK;BLANK;ND
            Case 2  ' Modal Hessenberg
               Print #1, "EIGRL   ";SID; V1; V2; ND; BLANK; MAXSET; SHFSCL; NORMALIZATION
               Print #1, "EIGC    ";SID;"    HESS";"     MAX";BLANK;BLANK;BLANK;ND
            End Select 

         End If

     'K & M computation
      Case  4

         BuildDMPEigrl SID, V1, V2, ND, MAXSET, SHFSCL, NORMALIZATION, LUMPED

         Print #1, "SOL 103"
         Print #1, "ECHOOFF $"
         Print #1, "COMPILE SEMODES $"
         Print #1, "ALTER 'MODEFSRS'(,-1)"
         Print #1, "OUTPUT2 MDICT,MELM,///92// $"
         Print #1, "OUTPUT2 KDICT,KELM,///91// $"
         Print #1, "EXIT"
         Print #1, "ECHOON $"
         Print #1, "CEND"

         Print #1, "BEGIN BULK"
         Print #1, "PARAM   GRDPNT         0"     ' Compute mass properties
         Print #1, "PARAM   K6ROT       10.0"     ' Normal rotation stiffness 
         Print #1, "PARAM   COUPMASS" & lumped    ' Use of lumped or coupled mass

     'Dynamic Sensitivity Analysis
      Case 12 

         BuildDMPEigrl SID, V1, V2, ND, MAXSET, SHFSCL, NORMALIZATION, LUMPED

         Print #1, "SOL 200"
         Print #1, "CEND"

         Print #1, "METHOD = ",SID
         Print #1, "DISPLACEMENT = ALL"
         Print #1, "DESSUB=1"

         Print #1, "$ SOL200 : exit after design sensitivity analysis and output"
         Print #1, "$          the sensitivity matrix (DSCM2 and DSCMCOL)"
         Print #1, "$"
         Print #1, "DSAPRT(NOPRINT,EXPORT,END=SENS)"
   '     Print #1, "PARAM,OPTEXIT,-4"     ' Alternative to DSAPRT
         Print #1, "$"

         Print #1, "$ SOL200 : define an analysis subcase in solution 200"
         Print #1, "$          function of choice or responses"
         Print #1, "$           "
   '-- What are the response types (Static or Modes)
   '-- Define the Subcase settings 
         Print #1, "SUBCASE 1"
         Print #1, "   ANALYSIS=MODES"
         Print #1, "$"

         Print #1, "BEGIN BULK"
         Print #1, "PARAM   POST          -5"     ' FEMtools interface files
         Print #1, "PARAM   GRDPNT         0"     ' Compute mass properties
         Print #1, "PARAM   K6ROT   " & IIF(k6rot < 0,"10.0",Format(Format(k6rot,"#.##E+##"),"@@@@@@@@"))     ' Normal rotation stiffness 
         Print #1, "PARAM   COUPMASS" & lumped   ' Use of lumped or coupled mass

         Print #1, "EIGRL   "; SID; V1; V2; ND; BLANK; MAXSET; SHFSCL; _
                   NORMALIZATION

         BuildDMPSol200

     'K computation
      Case  13 

         BuildDMPEigrl SID, V1, V2, ND, MAXSET, SHFSCL, NORMALIZATION, LUMPED

         Print #1, "SOL 103"
         Print #1, "ECHOOFF $"
         Print #1, "COMPILE SEMODES $"
         Print #1, "ALTER 'MODEFSRS'(,-1)"
         Print #1, "OUTPUT2 KDICT,KELM,///91// $"
         Print #1, "EXIT"
         Print #1, "ECHOON $"
         Print #1, "CEND"

         Print #1, "BEGIN BULK"
         Print #1, "PARAM   K6ROT       10.0"     ' Normal rotation stiffness 

      Case Else

         Ft_SetError "Analysis type not supported in nastran.bas"
         End

   End Select

 '-Include Unsupported BULK cards and PARAM's
   If (BackupDMP = True) Then

      Open DmpBack For Input As #2

     'Copy filtered lines back from the dump file
      fdel = False

      Do Until (EOF(2))
         Line Input #2,Text
         Card = UCase(Word(Text,1))
         If (Left(Text,1) = " " And fdel) Then
            fdel = False 'Skip this continuation card
         ElseIf (Card = "BEGIN") Then
            bPastBEGIN = True
         ElseIf (Card = "PARAM") Then
            Param = UCase(Word(Text,2))
            Select Case Param
               Case "POST", "K6ROT", "GRDPNT", "COUPMASS"
                  ' Skip these parameters
               Case "BAILOUT"
                  If bHasBailout = False Then Print #1, Text
               Case Else
                  Print #1,Text 'Include all PARAMs except POST, K6ROT, GRDPNT and COUPMASS
            End Select
            fdel = False
         ElseIf bPastBEGIN Then 'Include cards after BEGIN
            Select Case (Card)
               Case "EIGB","EIGC","EIGR","EIGRL","DESVAR","DCONSTR","DCONADD","DRESP1","DRESP2","DVMREL1","DVMREL2","DVPREL1","DVPREL2"
                  ' Skip these cards
                  fdel = True
               Case Else
                  If (Text <> "") Then
                     AddToModel = True
                     If (AnType = 11 And Card = "GRAV") Then AddToModel = False
                     If (AddToModel = True) Then Print #1,Text
                     fdel = False
                  End If
            End Select
         End If
         Loop
         Close #2
      End If

   Close #1

End Sub

'----

Sub ResetDMPFile

   If BackUpDMP Then
      Kill dmpfile
      FileCopy dmpback, dmpfile
   End If

End Sub


'--------------------------------------------------------------------------------------------------
' The main NASTRAN driver function.
' Remarks:
'   The variable AnType (AnalsysType) can have the following values:
'     0  = analysis type not specified 
'     1  = static analysis
'     2  = normal modes analysis (real and complex)
  '        Method = 1 Lanczos complex
'                   2 Hessenberg modal
'                   3 Inverse Power method
'                   4 Determinant method
'     4  = get stiffness and mass matrices only
'     11 = static displacement sensitivity analysis using pseudo-static loads
'     12 = Sensitivity analysis
'     13 = get stiffness only
'--------------------------------------------------------------------------------------------------
Sub Main()

 '-Declarations
   Dim StressX() As Double
   Dim StressY() As Double
   Dim StressZ() As Double
   Dim StressXY() As Double
   Dim StressZX() As Double
   Dim StressYZ() As Double
   Dim StressVM() As Double


 '-Initialization
   Ft_Report "Diagnostic","[DIAGNOSTICS] Starting NASTRAN driver."
   Ft_PutInt "Analysis.Status",0
   Ft_DefVar "analysis.solver.name", "nastran"
   DmpFile = "NASTRAN.DMP"
   DmpBack = "NASTRAN.BCK"
   BackUpDmp = 0
   Bdf = "nastran.tmp"
   Nas_NoMatrices = Ft_VarDef("Tune.Sensitivity",0)

 '-Retrieve data
   ArgV = Command()
   nArg = WordCount(ArgV)

   nElem  = Ft_GetCount("Element")
   AnType = Ft_GetInt("Analysis.Type")
   Method = Ft_GetVariant("Dynamic.Method")
   Reco   = Ft_GetVariant("Dynamic.Complex")

   nNode = Ft_GetCount("Node")

   Server = Trim(Ft_GetString("Analysis.Server"))
   IsSilent = Ft_GetInt("Console.Silent")
   Ft_Command "Set Silent On"

 '-Process arguments
   If (nArg >= 1) Then

     'Nastran file
      Bdf = Field(ArgV,1," ")
      Ft_Report "Diagnostic","[DIAGNOSTICS] Searching file: " & Bdf
      If (FileExists(Bdf) = False) Then
         Ft_SetError "Error: file '" & Bdf & "' does not exist."
         End
      End If

     'Parse keyword(s) and values (currently only 'server' allowed)
      i = 2
      Do While (i+1 <= nArg)
         If (LCase(Word(ArgV,i)) = "server") Then
            Server = Word(ArgV,i+1)
         ElseIf (LCase(Field(ArgV,i," ")) = "external") Then
            CheckExt = True
         End If
         i = i+2
      Loop
   Else
      If (nNode = 0) Then
         Ft_SetError "No FEM defined!"
         End
      End If
   End If

 '-Get the server label to read the ini file
   Server = LCase(Server)
   If (Server = "local") Then
      If (Left(OsVersion(),2) = "NT" Or Left(OsVersion(),3) = "WIN") Then
         Server = "local_nt"
      ElseIf (Left(OsVersion(),5) = "HP-UX") Then
         Server = "local_hp-ux"
      Else
         Server = "local_unix" 
      End If
   End If

 '-Read the Nastran ini file
   IniFile = BuildPath(MyDir(),"nastran.ini")
   SolSec = ReadIniSection(Server,IniFile)
   Ft_Report "Diagnostic","[DIAGNOSTICS] Searching file: " & IniFile
   If (FileExists(IniFile) = False) Then
      Ft_SetError "nastran.ini file does not exist!"
      End
   ElseIf (UBound(SolSec) = 0) Then
      Ft_SetError "Server " & server & " is not configured: see nastran.ini."
      End
   End If
   HostName = ReadIni(Server,"hostname",IniFile)
   Exec     = ReadIni(Server,"exec",    IniFile)
   Flags    = ReadIni(Server,"flags",   IniFile)
   WorkPath = ReadIni(Server,"workarea",IniFile)
   Unit11   = ReadIni(Server,"unit11",  IniFile)
   Unit51   = ReadIni(Server,"unit51",  IniFile)
   If (Nas_NoMatrices = True) Then Unit51 = "nastran.op2"
   Unit61   = ReadIni(Server,"unit61",  IniFile) 'reduced K
   Unit62   = ReadIni(Server,"unit62",  IniFile) 'reduced M
   Unit91   = ReadIni(Server,"unit91",  IniFile)
   Unit92   = ReadIni(Server,"unit92",  IniFile)
   Nas_sDisplacement = ReadIni(Server, "displacement", IniFile)

 '-Prepare Nastran input file in case issued by SET SOLVER
   If (nArg = 0) Then 

     'Prepare for static sensitivity analysis
      If (AnType = 11) Then
         Ft_Command "backup load force"
         Ft_Command "generate load sensitivity"
      End If

     'Prepare for dynamic sensitivity analysis
      If (AnType = 12) Then
         nParam = Ft_GetCount("Parameter")
        'Create new properties or materials when local param
         For iParam = 1 To nParam
            Ft_UpdateParam iParam,1.0,False
         Next iParam
      End If

     'Force Large Field Format for increased precision
      If (AnType = 4) Then 
         bNLrg = Ft_GetVariant("interface.nastran.large") 'Backup
         Ft_PutVariant "interface.nastran.large",True
      End If

     'Create the nastran file
      BuildDMPFile
      Ft_Export "fem","nastran.ascii",Bdf
      ResetDMPFile
      Call Nas_PostProcessing()

     'Restore default field format
      If (AnType = 4) Then 
         Ft_PutVariant "interface.nastran.large",bNLrg
      End If

     'Verification
      Ft_Report "Diagnostic","[DIAGNOSTICS] Searching file: " & BuildPath(CurDir(),Bdf)
      If (FileExists(Bdf) = False) Then
         Ft_SetError "Could not write file '" & Bdf & "'."
         End
      End If

   End If

 '-Print info to console
   iTune = Ft_GetInt("Tune.On")
   Dim nMode As Integer
   Ft_PutInt "Console.Silent",IsSilent

   Select Case (AnType)
     'Static Analysis, KM, Static Sensitivity, Dynamic Sensitivity
      Case 1,4,11,12,13
         If (iTune = 0) Then
            Ft_Report "Text","Using Nastran as external solver..."
         End If
     'Normal Modes Analysis (real and complex)
      Case 2
         If (iTune = 0) Then
            nMode = Ft_GetInt("Dynamic.Vectors")
            Freq1 = Ft_GetDouble("Dynamic.FMin")
            Freq2 = Ft_GetDouble("Dynamic.FMax")
            nNode = Ft_GetCount("Node")        ' number of Nodes
            nNDOF = Ft_GetCount("FEA.DOF")     ' number of DOF/node
            nADOF = nNode * nNDOF              ' number of Analysis DOFs
            Ft_Report "text", "Using Nastran as external solver..."
            If Len(Nas_sDomainSolver) Then Ft_Report "text", "Domain solver             : " & Nas_sDomainSolver
            Ft_Report "text", "Number of DOFs in model   : " & nADOF
            If nMode <> -1 Then sMode = CStr(nMode) Else sMode = "All"
            Ft_Report "text", "Number of requested modes : " & sMode
            Ft_Report "text", "Minimum frequency [Hz]    : " & Freq1
            Ft_Report "text", "Maximum frequency [Hz]    : " & Freq2
         End If
      Case Else
         Ft_SetError "Analysis type not supported!"
         End
   End Select
   Ft_PutInt "Console.Silent",1

 '-Launch Nastran
   Unit = IIf(AnType = 4 Or AnType = 13,Unit91,Unit51)

  'Static analysis
   If (AnType = 1 Or AnType = 11) Then
      If (Ft_GetInt("Static.Stress") <> 0) Then Unit = "nastran.op2"
   End If

  'Clean-up
   Kill Unit
   If (FileExists("nastran.f06") = True) Then Kill "nastran.f06"

  'Start Nastran
   If (Left(Server,5) = "local") Then
      Call LocalNastran(Bdf)
   Else
      If (Left(OsVersion(),2) = "NT" Or Left(OsVersion(),3) = "WIN") Then
         Call RemoteNastran_win(Bdf,HostName,WorkPath)
      Else
         Call RemoteNastran_unix(Bdf,HostName,WorkPath)
      End If
   End If

 '-Check results files
   Ft_Report "Diagnostic","[DIAGNOSTICS] Checking for results files."
   Ft_PutInt "Console.Silent",IsSilent
   Ft_Report "Diagnostic","[DIAGNOSTICS] Searching file: " & BuildPath(CurDir(),Unit)
   If (FileExists(Unit) = False And CheckExt = False) Then
      Ft_Report "Diagnostic","[DIAGNOSTICS] Searching file: " & BuildPath(CurDir(),"nastran.f06")
     'Report first FATAL error
      If (FileExists("nastran.f06") = True) Then 
         Ft_Command "set silent off"
         Ft_Report "Error","ERROR(s) occured during analysis : "
         Open "nastran.f06" For Input As #1
         nFatal = 0
         Do While (Not EOF(1))
            Line Input #1,Text
            If (InStr(Text,"USER FATAL") Or InStr(Text,"SYSTEM FATAL")) Then
               nFatal = nFatal + 1
               If (nFatal >= 5) Then
                  Ft_Report "Error",""
                  Ft_Report "Error","Error reporting aborted (too much errors)."
                  Ft_Report "Error",""
                  Exit Do
               End If
               Ft_Report "Error",Left(Text,60)
               Line Input #1,Text
               Ft_Report "Error",Left(Text,60)
            End If
         Loop
         If (nFatal > 0) Then Ft_Report "Error","See 'nastran.f06' file for more information."
         Close #1
      Else
         If (Search(Exec,"w\.exe") > 0) Then
            SplitPath Exec,,FileName
            Ft_Report "Error","Potential misconfiguration detected: '" & FileName & "' might be an inappropriate NASTRAN shell."
            Ft_Report "Error","For additional information on this topic consult the Troubleshooting section of the NASTRAN driver user's guide."
         End If
         Ft_SetError "No output file (nastran.f06) found."
         End
      End If
      Ft_SetError "Nastran analysis failed"
      End
   Else
      Ft_PutInt "analysis.status",1
   End If
   Ft_PutInt "Console.Silent",1

 '-Import results files in FEMtools database
   Ft_Report "Diagnostic","[DIAGNOSTICS] Importing results."
   If (nArg = 0) Then 

      Select Case (AnType)
        'Static Analysis
         Case 1 
         If (Ft_GetInt("Static.Stress") = 0) Then
            Ft_Report "Diagnostic","[DIAGNOSTICS] Searching file: " & BuildPath(CurDir(),Unit51)
            Ft_Import "Displacement","nastran.bin",Unit51
            Ft_Report "Diagnostic","[DIAGNOSTICS] Searching file: " & BuildPath(CurDir(),Unit91)
            Ft_Import "Stiffness","nastran.bin",Unit91
         Else
            Ft_Report "Diagnostic","[DIAGNOSTICS] Searching file: " & BuildPath(CurDir(),"nastran.op2")
            Ft_Import "Displacement","nastran.bin","nastran.op2"
            Ft_Report "Diagnostic","[DIAGNOSTICS] Searching file: " & BuildPath(CurDir(),Unit91)
            Ft_Import "Stiffness","nastran.bin",Unit91
            nElem = Ft_GetCount("Element")
            Has2D = False
            Has3D = False
            For iElem = 1 To nElem
               Ft_GetElem iElem,,EType
               Select Case (EType)
                  Case 4,5,6,7
                     Has2D = True
                  Case 8,9,10,11,12,13
                     Has3D = True
               End Select
            Next iElem
            Select Case (Ft_GetInt("Static.Stress"))
              'Stress
               Case 1
                  If (Has2D = True) Then
                    'Read nastran results
                     Ft_ReadOP2Shape "nastran.op2","fea.stress.x1","OES","SX1"
                     Ft_ReadOP2Shape "nastran.op2","fea.stress.x2","OES","SX2"
                     Ft_ReadOP2Shape "nastran.op2","fea.stress.y1","OES","SY1"
                     Ft_ReadOP2Shape "nastran.op2","fea.stress.y2","OES","SY2"
                     Ft_ReadOP2Shape "nastran.op2","fea.stress.xy1","OES","TXY1"
                     Ft_ReadOP2Shape "nastran.op2","fea.stress.xy2","OES","TXY2"
                    'Compute Von mises
                     Ft_GetLoad 1,ExtLoadId,Title
                     StressX = Ft_GetShape("fea.stress.x1",ExtLoadId)
                     StressY = Ft_GetShape("fea.stress.y1",ExtLoadId)
                     StressXY = Ft_GetShape("fea.stress.xy1",ExtLoadId)
                     StressVM = Sqrt(0.5 * ((StressX-StressY).^2+StressX.^2+StressY.^2) + 3*StressXY.^2)
                     Call Nas_ProcessShape(StressVM,"2D")
                     Ft_DefShape "fea.stress.vonmises.shell",112,2,,EG_FEM
                     Ft_PutShape "fea.stress.vonmises.shell",1,,StressVM
                     Ft_DefShapeText "fea.stress.vonmises.shell",1,"Stress at Z1"
                     StressX = Ft_GetShape("fea.stress.x2",ExtLoadId)
                     StressY = Ft_GetShape("fea.stress.y2",ExtLoadId)
                     StressXY = Ft_GetShape("fea.stress.xy2",ExtLoadId)
                     StressVM = Sqrt(0.5 * ((StressX-StressY).^2+StressX.^2+StressY.^2) + 3*StressXY.^2)
                     Call Nas_ProcessShape(StressVM,"2D")
                     Ft_PutShape "fea.stress.vonmises.shell",2,,StressVM
                     Ft_DefShapeText "fea.stress.vonmises.shell",2,"Stress at Z2"
                     Ft_SetShapeTitle "fea.stress.vonmises.shell","Von Mises Stress (Shell)"
                    'Combine the shapes Normal X
                     Ft_DefShape "fea.stress.x.shell",112,2,,EG_FEM
                     StressX = Ft_GetShape("fea.stress.x1",ExtLoadId)
                     Call Nas_ProcessShape(StressX,"2D")
                     Ft_PutShape "fea.stress.x.shell",1,,StressX
                     Ft_DefShapeText "fea.stress.x.shell",1,"Stress at Z1"
                     StressX = Ft_GetShape("fea.stress.x2",ExtLoadId)
                     Call Nas_ProcessShape(StressX,"2D")
                     Ft_PutShape "fea.stress.x.shell",2,,StressX
                     Ft_DefShapeText "fea.stress.x.shell",2,"Stress at Z2"
                     Ft_SetShapeTitle "fea.stress.x.shell","Normal Stress in X (Shell)"
                     Ft_ClearShape "fea.stress.x1"
                     Ft_ClearShape "fea.stress.x2"
                    'Combine the shapes Normal Y
                     Ft_DefShape "fea.stress.y.shell",112,2,,EG_FEM
                     StressY = Ft_GetShape("fea.stress.y1",ExtLoadId)
                     Call Nas_ProcessShape(StressY,"2D")
                     Ft_PutShape "fea.stress.y.shell",1,,StressY
                     Ft_DefShapeText "fea.stress.y.shell",1,"Stress at Z1"
                     StressY = Ft_GetShape("fea.stress.y2",ExtLoadId)
                     Call Nas_ProcessShape(StressY,"2D")
                     Ft_PutShape "fea.stress.y.shell",2,,StressX
                     Ft_DefShapeText "fea.stress.y.shell",2,"Stress at Z2"
                     Ft_SetShapeTitle "fea.stress.y.shell","Normal Stress in Y (Shell)"
                     Ft_ClearShape "fea.stress.y1"
                     Ft_ClearShape "fea.stress.y2"
                    'Combine the shapes Shear XY
                     Ft_DefShape "fea.stress.xy.shell",112,2,,EG_FEM
                     StressXY = Ft_GetShape("fea.stress.xy1",ExtLoadId)
                     Call Nas_ProcessShape(StressXY,"2D")
                     Ft_PutShape "fea.stress.xy.shell",1,,StressXY
                     Ft_DefShapeText "fea.stress.xy.shell",1,"Stress at Z1"
                     StressXY = Ft_GetShape("fea.stress.xy2",ExtLoadId)
                     Call Nas_ProcessShape(StressXY,"2D")
                     Ft_PutShape "fea.stress.xy.shell",2,,StressXY
                     Ft_DefShapeText "fea.stress.xy.shell",2,"Stress at Z2"
                     Ft_SetShapeTitle "fea.stress.xy.shell","Shear Stress in XY (Shell)"
                     Ft_ClearShape "fea.stress.xy1"
                     Ft_ClearShape "fea.stress.xy2"
                    'Refresh
                     Ft_Refresh "Shape","fea.stress.x.shell"
                     Ft_Refresh "Shape","fea.stress.y.shell"
                     Ft_Refresh "Shape","fea.stress.xy.shell"
                     Ft_Refresh "Shape","fea.stress.vonmises.shell"
                    'Clean up
                     Erase StressX
                     Erase StressY
                     Erase StressXY
                     Erase StressVM
                  End If
                  If (Has3D = True) Then
                    'Read nastran results
                     Ft_ReadOP2Shape "nastran.op2","fea.stress.x.solid","OES","EX"
                     Ft_ReadOP2Shape "nastran.op2","fea.stress.y.solid","OES","EY"
                     Ft_ReadOP2Shape "nastran.op2","fea.stress.z.solid","OES","EZ"
                     Ft_ReadOP2Shape "nastran.op2","fea.stress.xy.solid","OES","ETXY"
                     Ft_ReadOP2Shape "nastran.op2","fea.stress.zx.solid","OES","ETZX"
                     Ft_ReadOP2Shape "nastran.op2","fea.stress.yz.solid","OES","ETYZ"
                    'Compute Von mises
                     StressX = Ft_GetShape("fea.stress.x.solid",ExtLoadId)
                     StressY = Ft_GetShape("fea.stress.y.solid",ExtLoadId)
                     StressZ = Ft_GetShape("fea.stress.z.solid",ExtLoadId)
                     StressXY = Ft_GetShape("fea.stress.xy.solid",ExtLoadId)
                     StressZX = Ft_GetShape("fea.stress.zx.solid",ExtLoadId)
                     StressYZ = Ft_GetShape("fea.stress.yz.solid",ExtLoadId)
                     StressVM = Sqrt(0.5 * ((StressX-StressY).^2 + (StressY-StressZ).^2 + (StressZ-StressX).^2) + 3*StressXY.^2 + 3*StressZX.^2 + 3*StressYZ.^2)
                     Call Nas_ProcessShape(StressVM,"3D")
                     Ft_DefShape "fea.stress.vonmises.solid",112,1,,EG_FEM
                     Ft_PutShape "fea.stress.vonmises.solid",1,,StressVM
                     Ft_DefShapeText "fea.stress.vonmises.solid",1,"Stress at center"
                     Ft_SetShapeTitle "fea.stress.vonmises.solid","Von Mises Stress (Solid)"
                    'Clean-up normal stress
                     Call Nas_ProcessShape(StressX,"3D")
                     Ft_PutShape "fea.stress.x.solid",1,,StressX
                     Ft_SetShapeTitle "fea.stress.x.solid","Normal Stress in X (Solid)"
                     Ft_DefShapeText "fea.stress.x.solid",1,"Stress at center"
                     Call Nas_ProcessShape(StressY,"3D")
                     Ft_PutShape "fea.stress.y.solid",1,,StressY
                     Ft_SetShapeTitle "fea.stress.y.solid","Normal Stress in Y (Solid)"
                     Ft_DefShapeText "fea.stress.y.solid",1,"Stress at center"
                     Call Nas_ProcessShape(StressZ,"3D")
                     Ft_PutShape "fea.stress.z.solid",1,,StressZ
                     Ft_SetShapeTitle "fea.stress.z.solid","Normal Stress in Z (Solid)"
                     Ft_DefShapeText "fea.stress.z.solid",1,"Stress at center"
                    'Clean-up shear stress
                     Call Nas_ProcessShape(StressXY,"3D")
                     Ft_PutShape "fea.stress.xy.solid",1,,StressXY
                     Ft_SetShapeTitle "fea.stress.xy.solid","Shear Stress in XY (Solid)"
                     Ft_DefShapeText "fea.stress.xy.solid",1,"Stress at center"
                     Call Nas_ProcessShape(StressZX,"3D")
                     Ft_PutShape "fea.stress.zx.solid",1,,StressZX
                     Ft_SetShapeTitle "fea.stress.zx.solid","Normal Stress in ZX (Solid)"
                     Ft_DefShapeText "fea.stress.zx.solid",1,"Stress at center"
                     Call Nas_ProcessShape(StressYZ,"3D")
                     Ft_PutShape "fea.stress.yz.solid",1,,StressYZ
                     Ft_SetShapeTitle "fea.stress.yz.solid","Normal Stress in YZ (Solid)"
                     Ft_DefShapeText "fea.stress.yz.solid",1,"Stress at center"
                    'Refresh
                     Ft_Refresh "Shape","fea.stress.x.solid"
                     Ft_Refresh "Shape","fea.stress.y.solid"
                     Ft_Refresh "Shape","fea.stress.z.solid"
                     Ft_Refresh "Shape","fea.stress.xy.solid"
                     Ft_Refresh "Shape","fea.stress.zx.solid"
                     Ft_Refresh "Shape","fea.stress.yz.solid"
                     Ft_Refresh "Shape","fea.stress.vonmises.solid"
                  End If
              'Strain
               Case 3
                 'Read the Nastran results file
                  Ft_ReadOP2Shape "nastran.op2","fea.strain.x","OEE","EX1"
                  Ft_ReadOP2Shape "nastran.op2","fea.strain.y","OEE","EY1"
                  Ft_ReadOP2Shape "nastran.op2","fea.strain.xy","OEE","TXY1"
                  Ft_SetShapeTitle "fea.strain.x","Normal Strain in X"
                  Ft_SetShapeTitle "fea.strain.y","Normal Strain in Y"
                  Ft_SetShapeTitle "fea.strain.xy","Shear Strain in XY"
                 'Update the values of the strain responses
                  nResp = Ft_GetCount("Response")
                  For iResp = 1 To nResp
                     Ft_GetResponse iResp,RespType
                     If (RespType = 13) Then
                       'Convert the element straint to a strain in the local CS
                        Ft_GetRespStrain iResp,LoadId,ElemId,Direction,CSId
                        Ft_GetElem ElemId,ExtId,ElemType,NodeList
                        Select Case (ElemType)
                           Case ET_QUAD4
                             'Get the nodal positions
                              Ft_GetNode NodeList(1),,Node1
                              Ft_GetNode NodeList(2),,Node2
                              Ft_GetNode NodeList(3),,Node3
                              Ft_GetNode NodeList(4),,Node4
                             'Copmute the element X axis
                              Diag1 = Node3-Node1
                              Diag2 = Node2-Node4
                              XAxis = Diag1+Diag2
                              XAxis = XAxis/Norm(XAxis)
                             'Copmute the element Z axis
                              ZAxis = Cross(Diag2,Diag1)
                              ZAxis = ZAxis/Norm(ZAxis)
                             'Copmute the element Y axis
                              YAxis = Cross(ZAxis,XAxis)
                              YAxis = YAxis/Norm(YAxis)
                              Ft_GetCSMatrix RS_FEA,CSId,T
                              XDir = Squeeze(Cut(T,Range(1,3),1))
                             'Project Local X direction on the element
                              N = ZAxis
                              V = XDir
                              U = V - Dot(V,N)*N
                              U = U/Norm(U)
                             'Compute the angle between the projection and the element X axis
                              Theta = ACos(Dot(U,XAxis))*180/Pi
                              ThetaY = ACos(Dot(U,YAxis))*180/Pi
                              If (ThetaY > 90) Then Theta = -1*Theta
                             'Compute the local strain value
                              XStrain = Ft_GetShape("fea.strain.x",LoadId,,)
                              YStrain = Ft_GetShape("fea.strain.y",LoadId,,)
                              XYStrain = Ft_GetShape("fea.strain.xy",LoadId,,)
                              Ex = XStrain(ElemId)
                              Ey = YStrain(ElemId)
                              Exy = XYStrain(ElemId)
                              ExLoc = (Ex+Ey)/2 + (Ex-Ey)/2 * Cos(2*Theta/180*Pi) + Exy*Sin(2*Theta/180*Pi)
                              EyLoc = (Ex+Ey)/2 + (Ex-Ey)/2 * Cos(2*Theta/180*Pi) - Exy*Sin(2*Theta/180*Pi)
                              ExyLoc = (Ey-Ex)/2 * Sin(2*Theta/180*Pi) + Exy*Cos(2*Theta/180*Pi)
                              If (Direction = 1) Then StrainValue = ExLoc
                              If (Direction = 2) Then StrainValue = EyLoc
                              If (Direction = 3) Then StrainValue = ExyLoc
                           Case Else
                              Ft_SetError "Strain response element type not supported by driver."
                              End
                        End Select
                        Ft_SetRespProp iResp,RD_Current,StrainValue
                     End If
                  Next iResp
                 'Refresh the shapes
                  If (Ft_VarDef("Static.StrField") = True) Then
                    'Set the values that are not computed to NaN
                     SetId = Ft_VarDef("Static.SetId")
                     If (SetId <> 0) Then
                        ElemList = Range(1,Ft_GetCount("Element"))
                        Ft_GetSet Ft_Find("Set.Id",SetId),ExtId,SetType,SetList
                        ElemList = Exclude(ElemList,SetList)
                     End If
                     nElem = UBound(ElemList)
                     nShape = Ft_ShapeCount("fea.strain.x")
                     For iShape = 1 To nShape
                        XStrain = Ft_GetShape("fea.strain.x",iShape,,)
                        YStrain = Ft_GetShape("fea.strain.y",iShape,,)
                        XYStrain = Ft_GetShape("fea.strain.xy",iShape,,)
                        For iElem = 1 To nElem
                           XStrain(ElemList(iElem)) = NaN
                           YStrain(ElemList(iElem)) = NaN
                           XYStrain(ElemList(iElem)) = NaN
                        Next iElem
                        Ft_PutShape "fea.strain.x",iShape,,XStrain
                        Ft_PutShape "fea.strain.y",iShape,,YStrain
                        Ft_PutShape "fea.strain.xy",iShape,,XYStrain
                     Next iShape
                    'Update the plots
                     Ft_Refresh "Shape","fea.strain.x"
                     Ft_Refresh "Shape","fea.strain.y"
                     Ft_Refresh "Shape","fea.strain.xy"
                  Else
                     Ft_ClearShape "FEA.Strain.X"
                     Ft_ClearShape "FEA.Strain.Y"
                     Ft_ClearShape "FEA.Strain.XY"
                  End If
            End Select
         End If

        'Normal Modes Analysis
         Case 2 

           'Backup modal damping
            nEigV = Ft_ShapeCount("FEA.Mode")
            If (nEigV <> 0) Then 
               Dim mDamp(nEigV) As Double
               For i = 1 to nEigV
                  mDamp(i) = Ft_ShapeDamp("FEA.Mode",i)
               Next i
            End If

           'Import new analysis results
            Ft_Report "Diagnostic","[DIAGNOSTICS] Searching file: " & BuildPath(CurDir(),Unit51)
            Ft_Import "mode","nastran.bin",Unit51

            If UCase(Nas_sDomainSolver) = "ACMS" Then
               nFEAMode = Ft_ShapeCount("fea.mode")
               Nas_nRequestedMode = Ft_GetInt("dynamic.vectors")
               If nFEAMode > Nas_nRequestedMode Then
                  Ft_ClearShape "fea.mode", Range(Nas_nRequestedMode+1, nFEAMode), False
                  Ft_PutInt "console.silent", IsSilent
                  Ft_Report "text", "Trimming mode set from " & nFEAMode & " to " & Nas_nRequestedMode & " modes."
                  Ft_PutInt "console.silent", 1
               End If
               Ft_PutInt "dynamic.vectors", Nas_nRequestedMode
            End If

            If (nElem > 0 And Nas_NoMatrices = False) Then
               Ft_Report "Diagnostic","[DIAGNOSTICS] Searching file: " & BuildPath(CurDir(),Unit91)
               Ft_Import "stiffness","nastran.bin",Unit91
               If (FileExists(Unit92) = True) Then
                  Ft_Report "Diagnostic","[DIAGNOSTICS] Searching file: " & BuildPath(CurDir(),Unit92)
                  Ft_Import "mass","nastran.bin",Unit92
               End If
            End If
            nMDOF = Ft_GetCount("FEM.MDOF")
            CheckMaster = Ft_GetVariant("Dynamic.Master")
            If (CheckMaster = True And nMDOF <> 0) Then
               iSilent = Ft_GetInt("console.silent")
               Ft_PutInt "console.silent", 2
               Ft_Report "Diagnostic","[DIAGNOSTICS] Searching file: " & BuildPath(CurDir(),Unit61)
               Ft_Import "reduced stiffness","nastran.bin",Unit61
               Ft_Report "Diagnostic","[DIAGNOSTICS] Searching file: " & BuildPath(CurDir(),Unit62)
               Ft_Import "reduced mass","nastran.bin",Unit62
               Ft_PutInt "console.silent", iSilent
            End If

           'Restore modal damping
            nEigV2 = Ft_ShapeCount("FEA.Mode")
            For i = 1 To Min(Array(nEigV,nEigV2))
               Ft_DefShapeDamp "FEA.Mode",i,mDamp(i)
            Next i
            If (nEigV2 > nEigV And nEigV <> 0) Then
               For i = nEigV+1 To nEigV2
                  Ft_DefShapeDamp "FEA.Mode",i,mDamp(nEigV)
               Next i
            End If

        'Complex Modes Analysis
         Case 3 
           'Import new analysis results
            Ft_Report "Diagnostic","[DIAGNOSTICS] Searching file: " & BuildPath(CurDir(),Unit51)
            Ft_Import "mode","nastran.bin",Unit51
            Ft_Report "Diagnostic","[DIAGNOSTICS] Searching file: " & BuildPath(CurDir(),Unit91)
            Ft_Import "stiffness","nastran.bin",Unit91
            Ft_Report "Diagnostic","[DIAGNOSTICS] Searching file: " & BuildPath(CurDir(),Unit92)
            If (FileExists(Unit92) = True) Then
               Ft_Import "mass","nastran.bin",Unit92
            Else
               Ft_Command "Compute Mass" 
            End If

            nMODF = Ft_GetCount("FEM.MODF")
            If (nMDOF <> 0) Then
               Ft_Report "Diagnostic","[DIAGNOSTICS] Searching file: " & BuildPath(CurDir(),Unit61)
               Ft_Import "reduced stiffness","nastran.bin",Unit61
               Ft_Report "Diagnostic","[DIAGNOSTICS] Searching file: " & BuildPath(CurDir(),Unit62)
               Ft_Import "reduced mass","nastran.bin",Unit62
            End If

        'K & M matrices
         Case 4
            Ft_Report "Diagnostic","[DIAGNOSTICS] Searching file: " & BuildPath(CurDir(),Unit91)
            Ft_Import "stiffness","nastran.bin",Unit91
            Ft_Report "Diagnostic","[DIAGNOSTICS] Searching file: " & BuildPath(CurDir(),Unit92)
            If FileExists(Unit92) Then
               Ft_Import "mass","nastran.bin",Unit92
            Else
               Ft_Command "Compute Mass" 
            End If

        'Static Displacement Sensitivity Analysis
         Case 11
            Select Case (Ft_GetInt("Static.Stress"))
              'Displacements
               Case 0
                  SensType = Ft_GetInt("Sensitivity.Type")
                  SensTypes = Array("","Relative","Normalized","Density")
                  Ft_Report "Diagnostic","[DIAGNOSTICS] Searching file: " & BuildPath(CurDir(),Unit51)
                  Ft_Import "sensitivity","nastran.bin",Unit51    'NASTRAN sensitivities are ABSOLUTE
                  Ft_Command "Restore Load Force"
                  If (SensType <> 1) Then Ft_Command "Normalize Sensitivity " & SensTypes(SensType)
              'Stresses
               Case 1
                  Ft_Report "Diagnostic","[DIAGNOSTICS] Searching file: " & BuildPath(CurDir(),"nastran.op2")
                  nElem = Ft_GetCount("Element")
                  Has2D = False
                  Has3D = False
                  For iElem = 1 To nElem
                     Ft_GetElem iElem,,EType
                     Select Case (EType)
                        Case 4,5,6,7
                           Has2D = True
                        Case 8,9,10,11,12,13
                           Has3D = True
                     End Select
                  Next iElem

                  Dim StressSensX() As Double
                  Dim StressSensY() As Double
                  Dim StressSensZ() As Double
                  Dim StressSensXY() As Double
                  Dim StressSensZX() As Double
                  Dim StressSensYZ() As Double
                  Dim StressSensVM() As Double

                  If (Has2D = True) Then
                    'Read nastran results
                     Ft_ReadOP2Shape "nastran.op2","fea.stress.sens.x1","OES","SX1"
                     Ft_ReadOP2Shape "nastran.op2","fea.stress.sens.x2","OES","SX2"
                     Ft_ReadOP2Shape "nastran.op2","fea.stress.sens.y1","OES","SY1"
                     Ft_ReadOP2Shape "nastran.op2","fea.stress.sens.y2","OES","SY2"
                     Ft_ReadOP2Shape "nastran.op2","fea.stress.sens.xy1","OES","TXY1"
                     Ft_ReadOP2Shape "nastran.op2","fea.stress.sens.xy2","OES","TXY2"
                     nParam = Ft_ShapeCount("fea.stress.sens.x1")
                    'Compute Von mises
                     Ft_GetLoad 1,ExtLoadId,Title
                     StressX = Ft_GetShape("fea.stress.x.shell",1)
                     StressY = Ft_GetShape("fea.stress.y.shell",1)
                     StressXY = Ft_GetShape("fea.stress.xy.shell",1)
                     StressVM = Ft_GetShape("fea.stress.vonmises.shell",1)
                     Ft_DefShape "fea.stress.sens.vm.shell.z1",112,nParam,,EG_FEM
                     Ft_SetShapeTitle "fea.stress.sens.vm.shell.z1","Von Mises Stress Sensitivities at Z1 (Shell)"
                     For iParam = 1 To nParam
                        StressSensX = Ft_GetShape("fea.stress.sens.x1",iParam)
                        StressSensY = Ft_GetShape("fea.stress.sens.y1",iParam)
                        StressSensXY = Ft_GetShape("fea.stress.sens.xy1",iParam)
                        StressSensVM = 0.5*(StressVM).^(-1).*((2*StressX-StressY).*StressSensX + (2*StressY-StressX).*StressSensY + 6*StressXY.*StressSensXY)
                        Call Nas_ProcessShape(StressVM,"2D")
                        Ft_DefShapeText "fea.stress.sens.vm.shell.z1",iParam,"Parameter " & iParam
                        Ft_PutShape "fea.stress.sens.vm.shell.z1",iParam,,StressSensVM
                     Next iParam
                     StressX = Ft_GetShape("fea.stress.x.shell",2)
                     StressY = Ft_GetShape("fea.stress.y.shell",2)
                     StressXY = Ft_GetShape("fea.stress.xy.shell",1)
                     StressVM = Ft_GetShape("fea.stress.vonmises.shell",2)
                     Ft_DefShape "fea.stress.sens.vm.shell.z2",112,nParam,,EG_FEM
                     Ft_SetShapeTitle "fea.stress.sens.vm.shell.z2","Von Mises Stress Sensitivities at Z2 (Shell)"
                     For iParam = 1 To nParam
                        StressSensX = Ft_GetShape("fea.stress.sens.x2",iParam)
                        StressSensY = Ft_GetShape("fea.stress.sens.y2",iParam)
                        StressSensXY = Ft_GetShape("fea.stress.sens.xy2",iParam)
                        StressSensVM = 0.5*(StressVM).^(-1).*((2*StressX-StressY).*StressSensX + (2*StressY-StressX).*StressSensY + 6*StressXY.*StressSensXY)
                        Call Nas_ProcessShape(StressVM,"2D")
                        Ft_PutShape "fea.stress.sens.vm.shell.z2",iParam,,StressSensVM
                        Ft_DefShapeText "fea.stress.sens.vm.shell.z2",iParam,"Parameter " & iParam
                     Next iParam
                    'Combine the shapes Normal X
                     Ft_DefShape "fea.stress.sens.x.shell.z1",112,nParam,,EG_FEM
                     Ft_DefShape "fea.stress.sens.x.shell.z2",112,nParam,,EG_FEM
                     Ft_SetShapeTitle "fea.stress.sens.x.shell.z1","Normal Stress Sensitivities in X at Z1 (Shell)"
                     Ft_SetShapeTitle "fea.stress.sens.x.shell.z2","Normal Stress Sensitivities in X at Z2 (Shell)"
                     For iParam = 1 To nParam
                        StressX = Ft_GetShape("fea.stress.sens.x1",iParam)
                        Call Nas_ProcessShape(StressX,"2D")
                        Ft_PutShape "fea.stress.sens.x.shell.z1",iParam,,StressX
                        Ft_DefShapeText "fea.stress.sens.x.shell.z1",iParam,"Parameter " & iParam
                        StressX = Ft_GetShape("fea.stress.sens.x2",iParam)
                        Call Nas_ProcessShape(StressX,"2D")
                        Ft_PutShape "fea.stress.sens.x.shell.z2",iParam,,StressX
                        Ft_DefShapeText "fea.stress.sens.x.shell.z2",iParam,"Parameter" & iParam
                     Next iParam
                     Ft_ClearShape "fea.stress.sens.x1"
                     Ft_ClearShape "fea.stress.sens.x2"
                    'Combine the shapes Normal Y
                     Ft_DefShape "fea.stress.sens.y.shell.z1",112,nParam,,EG_FEM
                     Ft_DefShape "fea.stress.sens.y.shell.z2",112,nParam,,EG_FEM
                     Ft_SetShapeTitle "fea.stress.sens.y.shell.z1","Normal Stress Sensitivities in Y at Z1 (Shell)"
                     Ft_SetShapeTitle "fea.stress.sens.y.shell.z2","Normal Stress Sensitivities in Y at Z2 (Shell)"
                     For iParam = 1 To nParam
                        StressX = Ft_GetShape("fea.stress.sens.y1",iParam)
                        Call Nas_ProcessShape(StressX,"2D")
                        Ft_PutShape "fea.stress.sens.y.shell.z1",iParam,,StressX
                        Ft_DefShapeText "fea.stress.sens.y.shell.z1",iParam,"Parameter " & iParam
                        StressX = Ft_GetShape("fea.stress.sens.y2",iParam)
                        Call Nas_ProcessShape(StressX,"2D")
                        Ft_PutShape "fea.stress.sens.y.shell.z2",iParam,,StressX
                        Ft_DefShapeText "fea.stress.sens.y.shell.z2",iParam,"Parameter" & iParam
                     Next iParam
                     Ft_ClearShape "fea.stress.sens.y1"
                     Ft_ClearShape "fea.stress.sens.y2"
                    'Combine the shapes Shear XY
                     Ft_DefShape "fea.stress.sens.xy.shell.z1",112,nParam,,EG_FEM
                     Ft_DefShape "fea.stress.sens.xy.shell.z2",112,nParam,,EG_FEM
                     Ft_SetShapeTitle "fea.stress.sens.xy.shell.z1","Shear Stress Sensitivities in XY at Z1 (Shell)"
                     Ft_SetShapeTitle "fea.stress.sens.xy.shell.z2","Shear Stress Sensitivities in XY at Z2 (Shell)"
                     For iParam = 1 To nParam
                        StressX = Ft_GetShape("fea.stress.sens.xy1",iParam)
                        Call Nas_ProcessShape(StressX,"2D")
                        Ft_PutShape "fea.stress.sens.xy.shell.z1",iParam,,StressX
                        Ft_DefShapeText "fea.stress.sens.xy.shell.z1",iParam,"Parameter " & iParam
                        StressX = Ft_GetShape("fea.stress.sens.xy2",iParam)
                        Call Nas_ProcessShape(StressX,"2D")
                        Ft_PutShape "fea.stress.sens.xy.shell.z2",iParam,,StressX
                        Ft_DefShapeText "fea.stress.sens.xy.shell.z2",iParam,"Parameter" & iParam
                     Next iParam
                     Ft_ClearShape "fea.stress.sens.xy1"
                     Ft_ClearShape "fea.stress.sens.xy2"
                    'Refresh
                     Ft_Refresh "Shape","fea.stress.sens.x.shell.z1"
                     Ft_Refresh "Shape","fea.stress.sens.x.shell.z2"
                     Ft_Refresh "Shape","fea.stress.sens.y.shell.z1"
                     Ft_Refresh "Shape","fea.stress.sens.y.shell.z2"
                     Ft_Refresh "Shape","fea.stress.sens.xy.shell.z1"
                     Ft_Refresh "Shape","fea.stress.sens.xy.shell.z2"
                     Ft_Refresh "Shape","fea.stress.sens.vm.shell.z1"
                     Ft_Refresh "Shape","fea.stress.sens.vm.shell.z2"
                    'Clean up
                     Erase StressX
                     Erase StressSensX
                     Erase StressY
                     Erase StressSensY
                     Erase StressXY
                     Erase StressSensXY
                     Erase StressVM
                     Erase StressSensVM
                     Ft_Command "Restore Load Force"
                  End If
                  If (Has3D = True) Then
                    'Read nastran results
                     Ft_ReadOP2Shape "nastran.op2","fea.stress.sens.x.solid","OES","EX"
                     Ft_ReadOP2Shape "nastran.op2","fea.stress.sens.y.solid","OES","EY"
                     Ft_ReadOP2Shape "nastran.op2","fea.stress.sens.z.solid","OES","EZ"
                     Ft_ReadOP2Shape "nastran.op2","fea.stress.sens.xy.solid","OES","ETXY"
                     Ft_ReadOP2Shape "nastran.op2","fea.stress.sens.zx.solid","OES","ETZX"
                     Ft_ReadOP2Shape "nastran.op2","fea.stress.sens.yz.solid","OES","ETYZ"
                     nParam = Ft_ShapeCount("fea.stress.sens.x.solid")
                    'Compute Von mises
                     StressX = Ft_GetShape("fea.stress.x.solid",1)
                     StressY = Ft_GetShape("fea.stress.y.solid",1)
                     StressZ = Ft_GetShape("fea.stress.z.solid",1)
                     StressXY = Ft_GetShape("fea.stress.xy.solid",1)
                     StressZX = Ft_GetShape("fea.stress.zx.solid",1)
                     StressYZ = Ft_GetShape("fea.stress.yz.solid",1)
                     StressVM = Ft_GetShape("fea.stress.vonmises.solid",1)
                     Ft_DefShape "fea.stress.sens.vm.solid",112,nParam,,EG_FEM
                     Ft_SetShapeTitle "fea.stress.sens.vm.solid","Von Mises Stress Sensitivities (Solid)"
                     For iParam = 1 To nParam
                        StressSensX = Ft_GetShape("fea.stress.sens.x.solid",iParam)
                        StressSensY = Ft_GetShape("fea.stress.sens.y.solid",iParam)
                        StressSensZ = Ft_GetShape("fea.stress.sens.z.solid",iParam)
                        StressSensXY = Ft_GetShape("fea.stress.sens.xy.solid",iParam)
                        StressSensZX = Ft_GetShape("fea.stress.sens.zx.solid",iParam)
                        StressSensYZ = Ft_GetShape("fea.stress.sens.yz.solid",iParam)
                        StressSensVM = 0.5*(StressVM).^(-1).*((2*StressX-StressY-StressZ).*StressSensX + (2*StressY-StressX-StressZ).*StressSensY + (2*StressZ-StressX-StressY).*StressSensZ + 6*StressXY.*StressSensXY + 6*StressZX.*StressSensZX + 6*StressYZ.*StressSensYZ)
                        Call Nas_ProcessShape(StressVM,"3D")
                        Ft_DefShapeText "fea.stress.sens.vm.solid",iParam,"Parameter " & iParam
                        Ft_PutShape "fea.stress.sens.vm.solid",iParam,,StressSensVM
                     Next iParam
                    'Clean-up the shapes Normal X
                     Ft_SetShapeTitle "fea.stress.sens.x.solid","Normal Stress Sensitivities in X (Solid)"
                     For iParam = 1 To nParam
                        StressX = Ft_GetShape("fea.stress.sens.x.solid",iParam)
                        Call Nas_ProcessShape(StressX,"3D")
                        Ft_PutShape "fea.stress.sens.x.solid",iParam,,StressX
                        Ft_DefShapeText "fea.stress.sens.x.solid",iParam,"Parameter " & iParam
                     Next iParam
                    'Clean-up the shapes Normal Y
                     Ft_SetShapeTitle "fea.stress.sens.y.solid","Normal Stress Sensitivities in Y (Solid)"
                     For iParam = 1 To nParam
                        StressY = Ft_GetShape("fea.stress.sens.y.solid",iParam)
                        Call Nas_ProcessShape(StressY,"3D")
                        Ft_PutShape "fea.stress.sens.y.solid",iParam,,StressY
                        Ft_DefShapeText "fea.stress.sens.y.solid",iParam,"Parameter " & iParam
                     Next iParam
                    'Clean-up the shapes Normal Z
                     Ft_SetShapeTitle "fea.stress.sens.z.solid","Normal Stress Sensitivities in Z (Solid)"
                     For iParam = 1 To nParam
                        StressZ = Ft_GetShape("fea.stress.sens.z.solid",iParam)
                        Call Nas_ProcessShape(StressZ,"3D")
                        Ft_PutShape "fea.stress.sens.y.solid",iParam,,StressZ
                        Ft_DefShapeText "fea.stress.sens.y.solid",iParam,"Parameter " & iParam
                     Next iParam
                    'Clean-up the shapes Shear XY
                     Ft_SetShapeTitle "fea.stress.sens.xy.solid","Shear Stress Sensitivities in XY (Solid)"
                     For iParam = 1 To nParam
                        StressXY = Ft_GetShape("fea.stress.sens.xy.solid",iParam)
                        Call Nas_ProcessShape(StressXY,"3D")
                        Ft_PutShape "fea.stress.sens.xy.solid",iParam,,StressXY
                        Ft_DefShapeText "fea.stress.sens.xy.solid",iParam,"Parameter " & iParam
                     Next iParam
                    'Clean-up the shapes Shear ZX
                     Ft_SetShapeTitle "fea.stress.sens.zx.solid","Shear Stress Sensitivities in ZX (Solid)"
                     For iParam = 1 To nParam
                        StressZX = Ft_GetShape("fea.stress.sens.zx.solid",iParam)
                        Call Nas_ProcessShape(StressZX,"3D")
                        Ft_PutShape "fea.stress.sens.zx.solid",iParam,,StressZX
                        Ft_DefShapeText "fea.stress.sens.zx.solid",iParam,"Parameter " & iParam
                     Next iParam
                    'Clean-up the shapes Shear YZ
                     Ft_SetShapeTitle "fea.stress.sens.yz.solid","Shear Stress Sensitivities in YZ (Solid)"
                     For iParam = 1 To nParam
                        StressYZ = Ft_GetShape("fea.stress.sens.yz.solid",iParam)
                        Call Nas_ProcessShape(StressYZ,"3D")
                        Ft_PutShape "fea.stress.sens.yz.solid",iParam,,StressYZ
                        Ft_DefShapeText "fea.stress.sens.yz.solid",iParam,"Parameter " & iParam
                     Next iParam
                    'Refresh
                     Ft_Refresh "Shape","fea.stress.sens.x.solid"
                     Ft_Refresh "Shape","fea.stress.sens.y.solid"
                     Ft_Refresh "Shape","fea.stress.sens.z.solid"
                     Ft_Refresh "Shape","fea.stress.sens.xy.solid"
                     Ft_Refresh "Shape","fea.stress.sens.zx.solid"
                     Ft_Refresh "Shape","fea.stress.sens.yz.solid"
                     Ft_Refresh "Shape","fea.stress.sens.vm.solid"
                    'Clean up
                     Erase StressX
                     Erase StressSensX
                     Erase StressY
                     Erase StressSensY
                     Erase StressZ
                     Erase StressSensZ
                     Erase StressXY
                     Erase StressSensXY
                     Erase StressZX
                     Erase StressSensZX
                     Erase StressYZ
                     Erase StressSensYZ
                     Erase StressVM
                     Erase StressSensVM
                     Ft_Command "Restore Load Force"
                  End If
              'Strain
               Case 3
                 'Initialization
                  Dim S() As Double
                  SensType = Ft_GetInt("Sensitivity.Type")
                  nResp = Ft_GetCount("Response")
                 'Load the displacement sensitivities
                  For iResp = 1 To nResp
                     Ft_GetResponse iResp,RespType
                     If (RespType = RT_Disp) Then
                        Ft_Report "Diagnostic","[DIAGNOSTICS] Searching file: " & BuildPath(CurDir(),"nastran.op2")
                        Ft_Import "Sensitivity","nastran.bin","nastran.op2"
                        Exit For
                     End If
                  Next iResp
                 'Create the sensitivity matrix
                  nParam = Ft_GetCount("Parameter")
                  If (Ft_MatExists("Sensitivity") = True) Then
                     S = Ft_GetMat("Sensitivity")
                  Else
                     ReDim S(nResp,nParam) As Double
                  End If
                 'Load the strain sensitivities
                  Ft_ReadOP2Shape "nastran.op2","fea.strain.sens.x","OEE","EX1"
                  Ft_ReadOP2Shape "nastran.op2","fea.strain.sens.y","OEE","EY1"
                  Ft_ReadOP2Shape "nastran.op2","fea.strain.sens.xy","OEE","TXY1"
                  Ft_SetShapeTitle "fea.strain.sens.x","Normal Strain Sensitivities in X"
                  Ft_SetShapeTitle "fea.strain.sens.y","Normal Strain Sensitivities in Y"
                  Ft_SetShapeTitle "fea.strain.sens.xy","Shear Strain Sensitivities in XY"
                  Ft_Command "Restore Load Force"
                 'Compute the local stain sensitivities and store them in the sensitivity matrix
                  For iResp = 1 To nResp
                     Ft_GetResponse iResp,RespType
                     If (RespType = 13) Then
                       'Convert the element strain to a strain in the local CS
                        Ft_GetRespStrain iResp,LoadId,ElemId,Direction,CSId
                        Ft_GetElem ElemId,ExtId,ElemType,NodeList
                        Select Case (ElemType)
                           Case ET_QUAD4
                             'Get the nodal positions
                              Ft_GetNode NodeList(1),,Node1
                              Ft_GetNode NodeList(2),,Node2
                              Ft_GetNode NodeList(3),,Node3
                              Ft_GetNode NodeList(4),,Node4
                             'Copmute the element X axis
                              Diag1 = Node3-Node1
                              Diag2 = Node2-Node4
                              XAxis = Diag1+Diag2
                              XAxis = XAxis/Norm(XAxis)
                             'Copmute the element Z axis
                              ZAxis = Cross(Diag2,Diag1)
                              ZAxis = ZAxis/Norm(ZAxis)
                             'Copmute the element Y axis
                              YAxis = Cross(ZAxis,XAxis)
                              YAxis = YAxis/Norm(YAxis)
                              Ft_GetCSMatrix RS_FEA,CSId,T
                              XDir = Squeeze(Cut(T,Range(1,3),1))
                             'Project Local X direction on the element
                              N = ZAxis
                              V = XDir
                              U = V - Dot(V,N)*N
                              U = U/Norm(U)
                             'Compute the angle between the projection and the element X axis
                              Theta = ACos(Dot(U,XAxis))*180/Pi
                              ThetaY = ACos(Dot(U,YAxis))*180/Pi
                              If (ThetaY > 90) Then Theta = -1*Theta
                             'Compute the local strain value
                              XStrain = Ft_GetShape("fea.strain.sens.x",LoadId,,)
                              YStrain = Ft_GetShape("fea.strain.sens.y",LoadId,,)
                              XYStrain = Ft_GetShape("fea.strain.sens.xy",LoadId,,)
                              Ex = XStrain(ElemId)
                              Ey = YStrain(ElemId)
                              Exy = XYStrain(ElemId)
                              ExLoc = (Ex+Ey)/2 + (Ex-Ey)/2 * Cos(2*Theta/180*Pi) + Exy*Sin(2*Theta/180*Pi)
                              EyLoc = (Ex+Ey)/2 + (Ex-Ey)/2 * Cos(2*Theta/180*Pi) - Exy*Sin(2*Theta/180*Pi)
                              ExyLoc = (Ey-Ex)/2 * Sin(2*Theta/180*Pi) + Exy*Cos(2*Theta/180*Pi)
                              If (Direction = 1) Then StrainValue = ExLoc
                              If (Direction = 2) Then StrainValue = EyLoc
                              If (Direction = 3) Then StrainValue = ExyLoc
                           Case Else
                              Ft_SetError "Strain response element type not supported by driver."
                        End Select
                        S(iResp,1) = StrainValue
                     End If
                  Next iResp
                 'Normalize the sensitivity matrix
                  Ft_NewMat "sensitivity",S
                  Ft_PutInt "Sensitivity.Type",1
                  SensTypes = Array("","Relative","Normalized","Density")
                  If (SensType <> 1) Then Ft_Command "Normalize Sensitivity " & SensTypes(SensType)

                 'Refresh the shapes
                  If (Ft_VarDef("Static.StrField",True) = True) Then
                    'Set the values that are not computed to NaN
                     SetId = Ft_VarDef("Static.SetId")
                     If (SetId <> 0) Then
                        ElemList = Range(1,Ft_GetCount("Element"))
                        Ft_GetSet Ft_Find("Set.Id",SetId),ExtId,SetType,SetList
                        ElemList = Exclude(ElemList,SetList)
                     End If
                     nElem = UBound(ElemList)
                     For iShape = 1 To nShape
                        XStrain = Ft_GetShape("fea.strain.sens.x",iShape,,)
                        YStrain = Ft_GetShape("fea.strain.sens.y",iShape,,)
                        XYStrain = Ft_GetShape("fea.strain.sens.xy",iShape,,)
                        For iElem = 1 To nElem
                           XStrain(ElemList(iElem)) = NaN
                           YStrain(ElemList(iElem)) = NaN
                           XYStrain(ElemList(iElem)) = NaN
                        Next iElem
                        Ft_PutShape "fea.strain.sens.x",iShape,,XStrain
                        Ft_PutShape "fea.strain.sens.y",iShape,,YStrain
                        Ft_PutShape "fea.strain.sens.xy",iShape,,XYStrain
                     Next iShape
                    'Refresh the plots
                     Ft_Refresh "Shape","fea.strain.sens.x"
                     Ft_Refresh "Shape","fea.strain.sens.y"
                     Ft_Refresh "Shape","fea.strain.sens.xy"
                  Else
                     Ft_ClearShape "FEA.Strain.Sens.X"
                     Ft_ClearShape "FEA.Strain.Sens.Y"
                     Ft_ClearShape "FEA.Strain.Sens.XY"
                  End If
            End Select

        'Dynamic Sensitivity Analysis
         Case 12
            Ft_Report "Diagnostic","[DIAGNOSTICS] Searching file: " & BuildPath(CurDir(),Unit11)
            Ft_Import "sensitivity","nastran.bin",Unit11   'NASTRAN sensitivities are ABSOLUTE
            Ft_Command "normalize sensitivity"  

        'K matrices
         Case 13
            Ft_Report "Diagnostic","[DIAGNOSTICS] Searching file: " & BuildPath(CurDir(),Unit91)
            Ft_Import "stiffness","nastran.bin",Unit91

        'Unknown analysis type
         Case Else
            Ft_SetError "Analysis type not supported."
            End
      End Select

   End If

 '-Finalization
   Ft_PutInt "Console.Silent",IsSilent
   Ft_Report "Diagnostic","[DIAGNOSTICS] Exit NASTRAN driver."

End Sub






















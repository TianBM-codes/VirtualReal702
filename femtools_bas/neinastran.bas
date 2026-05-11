
'#neinastran.bas : Driver for NE/NASTRAN.

' Copyright (c) 2005 by Dynamic Design Solutions NV.

' Change history:
' 09 september 2005 : initial release

' Notes:
' - NENAST_EXE environment variable is used to determine nastran.exe path

'---

Dim nastran$, dmpfile$, dmpback$, op2unit$, o2dunit$
Dim BackUpDMP
Dim itune As Integer, antype As Integer, method As Integer
Dim Reco As Boolean

Sub BuildDMPEigrl(SID, V1, V2, ND, MAXSET, SHFSCL, NORMALIZATION, LUMPED)

   iVectors   = Ft_GetInt("dynamic.vectors")
   iNorm      = Ft_GetInt("dynamic.norm")
   iSize      = Ft_GetInt("dynamic.size")
   dFmin      = Ft_GetDouble("dynamic.fmin")
   dFmax      = Ft_GetDouble("dynamic.fmax")
   checklump  = Ft_GetVariant("compute.lumped")

   BLANK = "        "
   iMethod = 1 
   SID = Format(CStr(iMethod),"@@@@@@@@")

   V1 = Format(Format(dFmin,"#####0.#"),"@@@@@@@@")
   SHFSCL = BLANK

   If dFmax > 1.0E+06 Then 
      V2 = BLANK
   Else
      V2 = Format(Format(dFmax,"#.##E+##"),"@@@@@@@@") 
   End If

   ND = Format(CStr(iVectors),"@@@@@@@@")

   If iSize > 0 Then
      MAXSET = Format(CStr(iSize),"@@@@@@@@")
   Else
      MAXSET = BLANK
   End If

   Select Case iNorm
   Case 1
      NORMALIZATION = "     MAX"
      Print " Mass normalized eigenvectors required for sensitivity analysis!"
   Case 2
      NORMALIZATION = "    MASS"
   Case 3
      Print "Stiffness normalized eigenvectors not allowed in NEiNASTRAN!"
      Print "Mass normalization will be used!"
      NORMALIZATION = "    MASS"
   End Select

   If checklump Then 
      lumped = "      -1"
   Else
      lumped = "       1"
   End If

End Sub

Sub BuildDMPFile

   k6rot = Ft_GetDouble("fem.k6rot")

   If FileExists(dmpfile) Then 
      FileCopy dmpfile, dmpback
      Kill dmpfile
      BackUpDMP = 1
   End If

   Open dmpfile For Output As #1

   BLANK = "        "
   
   Select Case antype

   Case 1, 11 ' Static Analysis and Sensitivity Analysis Pseudo loads

      Print #1, "SOL 101"
      Print #1, "CEND"

      Print #1, "DISPLACEMENT = ALL"

      Print #1, "BEGIN BULK"
      Print #1, "PARAM   POST          -2"

   Case 2 ' Normal Modes Analysis

      If Not Reco Then

         BuildDMPEigrl SID, V1, V2, ND, MAXSET, SHFSCL, NORMALIZATION, LUMPED

         Print #1, "SOL 103"

' Write master DOF is existing in database

         nmdof = Ft_GetCount("fem.mdof")
         checkmaster = Ft_GetVariant("dynamic.master")
         If checkmaster And nmdof <> 0 Then
             Ft_SetError "Guyan reduction is not supported yet"
             End
         End If

         Print #1, "CEND"

         Print #1, "METHOD = ",SID
         Print #1, "DISPLACEMENT = ALL"

         Print #1, "BEGIN BULK"
         Print #1, "PARAM   POST          -2"     ' .op2 interface files
         Print #1, "PARAM   GRDPNT         0"     ' Compute mass properties        
         Print #1, "PARAM   K6ROT   " & IIF(k6rot < 0,"10.0",Format(Format(k6rot,"#.##E+##"),"@@@@@@@@"))     ' Normal rotation stiffness 
         Print #1, "PARAM   COUPMASS" & lumped   ' Use of lumped or coupled mass

         Print #1, "EIGRL   "; SID; V1; V2; ND; BLANK; MAXSET; SHFSCL; _
                   NORMALIZATION

      Else   ' Complex modes

         BuildDMPEigrl SID, V1, V2, ND, MAXSET, SHFSCL, NORMALIZATION, LUMPED

         Print #1, "SOL 110"
         Print #1, "CEND"

         Print #1, "METHOD = ",SID
         iMethod = 2
         SID2 = Format(CStr(iMethod),"@@@@@@@@")
         Print #1, "CMETHOD = ",SID2
         Print #1, "DISPLACEMENT = ALL"
         Print #1, "BEGIN BULK"
         Print #1, "PARAM   POST          -2"     ' .op2 interface files
         Print #1, "PARAM   GRDPNT         0"     ' Compute mass properties
         Print #1, "PARAM   K6ROT   " & IIF(k6rot < 0,"10.0",Format(Format(k6rot,"#.##E+##"),"@@@@@@@@"))     ' Normal rotation stiffness 
         Print #1, "PARAM   COUPMASS" & lumped   ' Use of lumped or coupled mass

         Print #1, "EIGRL   "; SID; V1; V2; ND; BLANK; MAXSET; SHFSCL; _
                   NORMALIZATION

         Select Case method
         Case 1  ' Lanczos complex
            Print #1, "EIGC    ";SID2;"    CLAN";"     MAX";BLANK;BLANK;BLANK;ND
         Case 2  ' Modal Hessenberg
            Print #1, "EIGC    ";SID2;"    HESS";"     MAX";BLANK;BLANK;BLANK;ND
         Case 3  ' Inverse Power
            Print #1, "EIGC    ";SID2;"     INV";"     MAX"';blank;blank;blank;ND
            ZERO = "      0."
            ONE  = "      1."
            Print #1, BLANK;ZERO;ZERO;"     10.";"     10.";ONE;"       0";ND
         Case 4  ' Determinant
            Print #1, "EIGC    ";SID2;"     DET";"     MAX"';blank;blank;blank;ND
            ZERO = "      0."
            ONE  = "      1."
            Print #1, BLANK;ZERO;ZERO;"     10.";"     10.";ONE;"      50";ND
         End Select 

      End If

   Case  4 ' K & M computation

      BuildDMPEigrl SID, V1, V2, ND, MAXSET, SHFSCL, NORMALIZATION, LUMPED

      Print #1, "BEGIN BULK"
      Print #1, "PARAM   GRDPNT         0"     ' Compute mass properties
      Print #1, "PARAM   K6ROT       10.0"     ' Normal rotation stiffness 
      Print #1, "PARAM   COUPMASS" & lumped    ' Use of lumped or coupled mass

   Case Else

      Ft_SetError "Analysis type not supported in nastran.bas"
      End

   End Select

'  Include Unsupported BULK cards and PARAM's

   If BackUpDMP Then
      Open dmpback For Input As #2
      iBEGIN = 0

'  Copy filtered lines back from the dump file

      fdel = FALSE
      While Not Eof(2)
         Line Input #2, text$
         card = UCase(word(text,1))
  If Left(text, 1) = " " And fdel Then
            fdel = FALSE
         ElseIf card = "BEGIN" Then
            iBEGIN = 1
         ElseIf card = "PARAM" Then
            param = UCase(word(text,2))
            If (param <> "POST" ) And _
               (param <> "K6ROT") And _
               (param <> "GRDPNT") And _
               (param <> "K6ROT") And _
               (param <> "COUPMASS") Then 'Optionally, add more filters here
               Print #1, text ' include all PARAMs except POST,K6ROT,GRDPNT and COUPMASS
            End If
               fdel = FALSE
         ElseIf iBEGIN Then ' INCLUDE cards after BEGIN
            If Left(card, 3) = "EIG" Then
               ' Filter this card away
          fdel = TRUE
            ElseIf Left(card,3) = "DES" Then
               fdel = TRUE
            ElseIf Left(card,3) = "DCO" Then
               fdel = TRUE
            ElseIf Left(card,3) = "DRE" Then
               fdel = TRUE
            ElseIf Left(card,3) = "DVM" Then
               fdel = TRUE
            ElseIf Left(card,3) = "DVP" Then
               fdel = TRUE
            ElseIf text$ <> "" Then
               Print #1, text$
               fdel = FALSE
            End If
         End If
      Wend
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

'----

Sub Main()

   Ft_PutInt "analysis.status",0
   Ft_DefVar "analysis.solver.name", "nastran"

   dmpfile = "NASTRAN.DMP"
   dmpback = "NASTRAN.BCK"
   BackUpDMP = 0
   bdf$ = "nastran.nas"

   antype = Ft_GetInt("analysis.type")
   method = Ft_GetVariant("dynamic.method")
   Reco   = Ft_GetVariant("dynamic.complex")

   nnode = Ft_GetCount("node")

' For antype =
'   0  = analysis type not specified 
'   1  = static analysis
'   2  = normal modes analysis (real and complex)
'   Method = 1 Lanczos complex
'          2 Hessenberg modal
'          3 Inverse Power method
'          4 Determinant method
'   4  = get stiffness and mass matrices only
'   11 = static displacement sensitivity analysis using
'        pseudo-static loads

   If nnode = 0 Then
      Ft_SetError "No FEM defined!"
      End
   End If
  
   NENAST_EXE = environ("NENAST_EXE")
   If NENAST_EXE = "" Then
      Ft_SetError "NENAST_EXE environment variable not defined!"
      End
   End IF

   SplitPath NENAST_EXE, nepath
   nastran$ = BuildPath(nepath, "Nastran.exe")

   If Not FileExists(nastran$) Then
      Ft_SetError "Could Not Find '" & nastran$ & "'"
      End
   End If

   op2unit$ = "nastran.op2"
   o2dunit$ = "nastran.o2d"

   islnt = Ft_GetInt("console.silent") ' 1 if in silent console mode
   Ft_Command "set silent on"

' Prepare Nastran input file in case issued by SET SOLVER

   If antype = 11 Then     'Prepare for Sensitivity Analysis
      Ft_Command "backup load force"
      Ft_Command "generate load sensitivity"
   End If

   If antype = 4 Then ' Force Large Field Format for increased precision
      bnlrg = Ft_GetVariant("interface.nastran.large") ' Backup
      Ft_PutVariant "interface.nastran.large", TRUE
   End If

   BuildDMPFile
   Ft_Export "fem", "nastran.ascii", bdf$
   ResetDMPFile
  
   If antype = 4 Then ' Restore default field format
      Ft_PutVariant "interface.nastran.large", bnlrg
   End If

   If Not FileExists(bdf$) Then
      Ft_SetError "Could not write file '" & bdf & "'"
      End
   End If

' Print info to console

   itune = Ft_GetInt("tune.on")         ' 1 if in tune mode


   Dim nreq As Integer

   Select Case antype
   Case 1,4,11,12 ' Static Analysis, KM, Static Sensitivity, Dynamic Sensitivity
      If itune = 0 and islnt = 0 Then
         print "Using NEiNASTRAN as external solver..."
      End If
   Case 2 ' Normal Modes Analysis (real and complex)

         If itune = 0 and islnt = 0 Then
            nreq = Ft_GetInt("dynamic.vectors")
            f1   = Ft_GetDouble("dynamic.fmin")
            f2   = Ft_GetDouble("dynamic.fmax")
            nnode = Ft_GetCount("node")        ' number of NODES
            nndof = Ft_GetCount("fea.dof")     ' number of DOF/node
            nadof = nnode * nndof              ' number of Analysis DOFs

            print "Using NEiNASTRAN as external solver..."
            print "Number of DOFs in model   : "; nadof
            print "Number of requested modes : "; nreq
            print "Minimum frequency [Hz]    : "; f1
            print "Maximum frequency [Hz]    : "; f2
         End If
   Case Else
        Ft_SetError "Analysis type not supported!"
   End Select

   unit$ = IIf(antype=4,o2dunit$,op2unit$)

   ' Cleanup before NEiNASTRAN run :

   Kill "nastran.sta"
   Kill "nastran.xyp"
   Kill "nastran.dat"
   Kill "nastran.out"
   Kill "nastran.pch"
   Kill "nastran.rsf"
   Kill "nastran.log"
   Kill "license.log"
   Kill "nastran.op2"
   Kill "nastran.o2d"

   If itune = 0 Then Print "Running NEiNASTRAN solver locally..."

   ' Launch NEiNASTRAN

   if antype = 4 then ' stop analysis after K & M computation
       pcontrol$=" PROCESSCONTROL(GEOMPRCS)=TERMINATE"
   else
       pcontrol$=""
   end if

   ndir$ = curdir ' use current directory for result files.

   if itune = 0 then print "NEINASTRAN => ";nastran

   Ft_Exec """" & nastran & """" & " RSLTFILETYPE=NASTRANBINARY" & _
                                   " PURGE=OFF"                  & _
                                   " RSLTFILEPURGE=OFF"          & _
                                   " FILESIGNATURE=0000"         & _
                                   " FILESPEC1=" & NDIR          & _
                                   " FILESPEC2=" & NDIR          & _
                                   " FILESPEC3=" & NDIR          & _
                                   " FILESPEC4=" & NDIR          & _
               pcontrol$                     & _
                                   " " & bdf$

   ' Cleanup after NEiNASTRAN run :

   Kill "ml*.dat"
   Kill "sl*.dat"
   Kill "rs*.dat"
   Kill "nastran.rsf"
   Kill "nastran.sta"
   Kill "license.log"
   Kill "nastran.fno"
   Kill "gm180000.dat"
   Kill "gm210000.dat"
   Kill "gm220000.dat"
   Kill "nastran.dat"

   ' Check results files

   If Not FileExists(unit$) Then
      If FileExists("nastran.out") Then ' Report first FATAL error
    Ft_Command "set silent off"
    print " "
    print "ERROR(s) occurred during analysis : "
    print " "
         Open "nastran.out" For Input As #1
    nfatal = 0
         Do While Not Eof(1)
            Line Input #1, text$
       If InStr(text$, "FATAL ERROR") Then
               nfatal = nfatal + 1
          If (nfatal >= 5) Then
        print
        print "Error reporting aborted (too much errors)."
        Exit Do
          End If
          print Left$(text,80)
               Line Input #1, text$
          print Left$(text,80)
       End if
         Loop
         If (nfatal > 0) Then print "See 'nastran.out' file for more information."
         Close #1
      End If
      Ft_SetError "NEiNASTRAN analysis failed"
      End
   Else 
      Ft_PutInt "analysis.status",1
   End If

' Import results files in FEMtools database

   Select Case antype

   Case 1 ' Static Analysis

     Ft_Import "displacement", "nastran.bin", op2unit$
     Ft_Run "ro2d", o2dunit$ & " k"

   Case 2 ' Normal Modes Analysis

'    Backup modal damping

     neigv = Ft_ShapeCount("fea.mode")
     If neigv <> 0 Then 
        Dim mdamp(neigv) as Double
        For i = 1 to neigv
           mdamp(i) = Ft_ShapeDamp("fea.mode",i)
        Next i
     End If

'    Import new analysis results

     Ft_Import "mode", "nastran.bin", op2unit$
     Ft_Run "ro2d", o2dunit$ & " k"
     Ft_Run "ro2d", o2dunit$ & " m"

     nmdof = Ft_GetCount("fem.mdof")
     checkmaster = Ft_GetVariant("dynamic.master")
     If checkmaster And nmdof <> 0 Then
        Ft_SetError "Guyan reduction is Not Supported yet"
        End
     End If

'    Restore modal damping

     neigv2 = Ft_ShapeCount("fea.mode")
     For i = 1 to min(Array(neigv,neigv2))
         Ft_DefShapeDamp "fea.mode", i, mdamp(i)
     Next i
     If neigv2 > neigv AND neigv <> 0 Then
        For i = neigv+1 To neigv2
           Ft_DefShapeDamp "fea.mode", i, mdamp(neigv)
        Next i
     End If

   Case 4 ' K & M matrices

     Ft_Run "ro2d", o2dunit$ & " k"
     Ft_Run "ro2d", o2dunit$ & " m"

   Case 11 ' Static Displacement Sensitivity Analysis

     Ft_Import "sensitivity", "nastran.bin", op2unit$
     Ft_Command "restore load force"
     Ft_Command "normalize sensitivity"
     'NASTRAN sensitivities are ABSOLUTE

   Case Else

     Ft_SetError "Analysis type not supported."

   End Select

   ' Terminate

   If islnt = 0 Then Ft_Command "set silent off"

End Sub












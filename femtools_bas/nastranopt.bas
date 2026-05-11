'#nastranopt.bas : Example to make a driver for nastran optimization

' Copyright (c) 2008 by Dynamic Design Solutions NV.


'==================================================================================================

Dim DMPFile As String
Dim DMPBack As String
Dim BackUpDMP As Integer

'--------------------------------------------------------------------------------------------------
' Add the parameters and response information to the DMP file
'--------------------------------------------------------------------------------------------------
Sub BuildDMPSol200()

 '-Initialization
   np = Ft_GetCount("parameter")
   nr = Ft_GetCount("response")
   Blank = "        "

 '-SOL200 : define the design variables
  'DESVAR      ID   LABEL   XINIT     XLB     XUB   DELXV

   For i = 1 To np
      Ft_GetParameter i,Level,pType,Item,Confidence,Lower,Upper
      d = Ft_GetParamValue(i)

      Select Case (pType)
         Case PA_IZ ' IZ (Bending moment of inertia round Z)
            label = "      IZ"
         Case PA_IX ' IX (Torsional moment of inertia)
            label = "      IX"
         Case PA_IY ' IY (Bending moment of inertia)
            label = "      IY"
         Case PA_AX ' AX (Beam cross section)
            label = "      AX"
         Case PA_H  ' H (shell thickness)
            label = "       H"
         Case PA_E  ' E (Youngs modulus for isotropic materials)
            label = "       E"
         Case PA_RHO ' RHO (mass density)
            label = "     RHO"
       '-Other parameter types
         Case Else
            Ft_SetError "Parameter type id " & pType & _
                        " not supported for DESVAR (NastranOpt.bas)."
      End Select

      Id = Format(CStr(i),"@@@@@@@@")
      XInit = Format(Format(d,"#.##E+##"),"@@@@@@@@")
      XLB = "        "
      XUB = "        "
      DELXV = "        "
      
      Print #1, "DESVAR  ";Id;label;XInit;XLB;XUB;DELXV

   Next i

 '-SOL200 : relate the design variables to analysis model properties
  'DVPREL1      RID    TYPE     PID     FID    PMIN    PMAX      CO 
  '           DVID1   COEF1   DVID2   COEF2
  '
  'DVMREL1      RID    TYPE    RMID  MPNAME   MPMIN   MPMAX      CO 
  '           DVID1   COEF1   DVID2   COEF2

   For i = 1 To np
      Ft_GetParameter i,Level,pType,Item,Confidence,Lower,Upper

      Select Case (pType)
         Case PA_IZ     'IZ (Bending moment of inertia round Z)
            GeoMat = 1 '1 for geo, 2 for material
            pType =  "    PBAR"
            FId =    "       5"
         Case PA_IX
            GeoMat = 1
            pType =  "    PBAR"
            FId =    "       7"
         Case PA_IY
            GeoMat = 1
            pType =  "    PBAR"
            FId =    "       6"
         Case PA_AX
            GeoMat = 1
            pType =  "    PBAR"
            FId =    "       4"
         Case PA_H
            GeoMat = 1
            pType =  "  PSHELL"
            FId =    "       4"
         Case PA_E
            GeoMat = 2
            pType =  "    MAT1"
            MPName = "       E"
         Case PA_RHO
            GeoMat = 2
            pType =  "    MAT1"
            MPName = "     RHO"
       '-Other parameter types
         Case Else
            Ft_SetError "Parameter type id" & pType & _
                        " not supported for DVPREL1 (NastranOpt.bas)."
      End Select
     
    '-search corresponding property or material
      Select Case (Level)
         Case 1 'global parameter
            set_int_id = Ft_Find("set.id",Item)
            Ft_GetSet set_int_id,,,ElList
            Ft_GetElem ElList(1),,,,MatId,GeoId
            PId = Format(CStr(GeoId),"@@@@@@@@")
            RMId = Format(CStr(MatId),"@@@@@@@@")
         Case 2 'local parameter
            Ft_GetElem Item,,,,MatId,GeoId
            PId = Format(CStr(GeoId),"@@@@@@@@")
            RMId = Format(CStr(MatId),"@@@@@@@@")
      End Select

      RId = Format(CStr(i),"@@@@@@@@")
      PMin = "        "
      PMax = "        "
      CO = "        "
      DVId1 = RId
      Coef1 = "     1.0"
      DVId2 = Blank
      Coef2 = Blank

      Select Case (GeoMat)
         Case 1
            Print #1, "DVPREL1 ";RId;pType;PId;FId;PMin;PMax;CO 
            Print #1, Blank;DVId1;Coef1;DVId2;Coef2 
         Case 2
            Print #1, "DVMREL1 ";RId;pType;RMid;MPName;PMin;PMax;CO 
            Print #1, Blank;DVid1;Coef1;DVId2;Coef2 
      End Select
   Next i

 '-SOL200 : identify the analysis responses to be used in the design model
  '
  'DRESP1      ID   LABEL   RTYPE   PTYPE  REGION    ATTA

   Dim RespMat(nr) As Boolean 'array to mark if a response is to be included (yes when paired)
   For i = 1 To nr
      Ft_GetResponse i,rType,rSource,Confidence,Value

      Select Case (rType)
         Case RT_FREQ     ' Resonance frequency (Hz)
            If rSource = 1 then
               Ft_GetRespFreq i,iMode       
            Else
               Ft_GetRespFreq -i,iMode  ' using -i returns paired FE mode, alternatively use Ft_GetPair
            End if

            Id     = Format(CStr(i),"@@@@@@@@")
            Label  = "F" & CStr(1000000+imode)
            rType  = "    FREQ"    ' RTYPE=FREQ  natural frequency (Hz)
            pType  = "        " 
            Region = "        "
            ATTA   = Format(CStr(imode),"@@@@@@@@")
       '-Other response types
         Case Else
            Ft_SetError "Response type id " & rType & _
                        " not supported for DRESP1 (NastranOpt.bas)."
      End Select
     
      If (iMode) <> 0 Then 
         RespMat(i) = True
         Print #1, "DRESP1  ";Id;Label;rType;pType;Region;ATTA
      End If
   Next i

 '-DCONSTR to bind all DRESP to a common DESSUB id
  'DCONSTR   DCID     RID  LALLOW  UALLOW"

   For i = 1 To nr
      DCId = "       1"       'DESSUB = 1
      RId = Format(CStr(i),"@@@@@@@@")
      LAllow = "    1E30"
      UAllow = "    1E30"
      If (RespMat(i)) Then 
         Print #1, "DCONSTR ";DCId;RId;LAllow;UAllow
      End If
   Next i

End Sub


'--------------------------------------------------------------------------------------------------
' Write the DMP file with the configuration/analysis information
'--------------------------------------------------------------------------------------------------
Sub BuildDMPFile()

 '-Initialization
   k6rot = Ft_GetDouble("fem.k6rot")

   If FileExists(DMPFile) Then 
      FileCopy DMPFile,DMPBack
      Kill DMPFile
      BackUpDMP = 1
   End If

   Open DMPFile For Output As #1

   Blank = "        "
   
 '-Write nastran header
   Print #1, "SOL 200"
   Print #1, "CEND"

   Print #1, "METHOD = ",SID
   Print #1, "DISPLACEMENT = ALL"
   Print #1, "DESSUB=1"

   Print #1, "DSAPRT(NOPRINT,EXPORT,END=SENS)"
   Print #1, "$"

   Print #1, "$ SOL200 : define an analysis subcase in solution 200"
   Print #1, "$          function of choice or responses"
   Print #1, "$           "

   Print #1, "SUBCASE 1"
   Print #1, "   ANALYSIS=MODES"
   Print #1, "$"

   Print #1, "BEGIN BULK"
   Print #1, "PARAM   POST          -5"     ' FEMtools interface files
   Print #1, "PARAM   GRDPNT         0"     ' Compute mass properties
   Print #1, "PARAM   K6ROT   " & IIF(k6rot < 0,"10.0",Format(Format(k6rot,"#.##E+##"),"@@@@@@@@"))     ' Normal rotation stiffness 
   Print #1, "PARAM   COUPMASS" & lumped    ' Use of lumped or coupled mass

   Call BuildDMPSol200() 'Adding the parameter and responses to the DMP file

 '-Include Unsupported BULK cards and PARAM's
   If (BackUpDMP) Then
      Open DMPBack For Input As #2
      iBegin = 0

     'Copy filtered lines back from the dump file
      fDel = False
      While (Not EOF(2))
         Line Input #2,Text
         Card = UCase(Word(Text,1))
         If (Left(text, 1) = " " And fDel) Then
            fDel = False
         ElseIf (Card = "BEGIN") Then
            iBegin = 1
         ElseIf (Card = "PARAM") Then
            Param = UCase(Word(Text,2))
            If (Param <> "POST" ) And _
               (Param <> "K6ROT") And _
               (Param <> "GRDPNT") And _
               (Param <> "K6ROT") And _
               (Param <> "COUPMASS") Then 'Optionally, add more filters here
               Print #1,Text              ' include all PARAMs except POST,K6ROT,GRDPNT and COUPMASS
            End If
               fDel = False
         ElseIf iBegin Then ' INCLUDE cards after BEGIN
            If (Left(Card,3) = "EIG") Then
               ' Filter this card away
               fDel = True
            ElseIf (Left(Card,3) = "DES") Then
               fDel = True
            ElseIf (Left(Card,3) = "DCO") Then
               fDel = True
            ElseIf (Left(Card,3) = "DRE") Then
               fDel = True
            ElseIf (Left(Card,3) = "DVM") Then
               fDel = True
            ElseIf (Left(Card,3) = "DVP") Then
               fDel = True
            ElseIf (Text <> "") Then
               Print #1,Text
               fDel = False
            End If
         End If
      WEnd
      Close #2
   End If

   Close #1

End Sub


'--------------------------------------------------------------------------------------------------
' Clean-up of the temporary files
'--------------------------------------------------------------------------------------------------
Sub ResetDMPFile()
   If (BackUpDMP) Then
      Kill DMPFile
      FileCopy DMPBack,DMPFile
   End If
End Sub


'--------------------------------------------------------------------------------------------------
' Main script function
'--------------------------------------------------------------------------------------------------
Sub Main()

 '-Initialization
   DMPFile = "NASTRAN.DMP"
   DMPBack = "NASTRAN.BCK"
   BackUpDMP = 0
   BDFFile = "nastran.tmp"

 '-Write NASTRAN file
   Call BuildDMPFile()                      'Create a DMP file with the configuration/analysis data
   Ft_Export "fem","nastran.ascii",BDFFile  'Add the bulk data to the DMP file.
   Call ResetDMPFile()                      'Clean-up

 '-Finalize
   Print "Done."

 '-Open file
   Ft_Command "Edit nastran.tmp"

End Sub











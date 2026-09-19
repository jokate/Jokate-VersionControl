// Jokate revision control plugin for Unreal Engine.

#pragma once

#include "CoreMinimal.h"
#include "ISourceControlRevision.h"

/**
 * Jokate 의 '확정 버전' 하나를 가리키는 리비전.
 * 파일 내용은 데몬의 POST /api/extract 로 꺼내 온다.
 */
class FJokateSourceControlRevision : public ISourceControlRevision
{
public:
	// ISourceControlRevision
	virtual bool Get(FString& InOutFilename, EConcurrency::Type InConcurrency = EConcurrency::Synchronous) const override;
	virtual bool GetAnnotated(TArray<FAnnotationLine>& OutLines) const override;
	virtual bool GetAnnotated(FString& InOutFilename) const override;
	virtual const FString& GetFilename() const override;
	virtual int32 GetRevisionNumber() const override;
	virtual const FString& GetRevision() const override;
	virtual const FString& GetDescription() const override;
	virtual const FString& GetUserName() const override;
	virtual const FString& GetClientSpec() const override;
	virtual const FString& GetAction() const override;
	virtual TSharedPtr<ISourceControlRevision, ESPMode::ThreadSafe> GetBranchSource() const override;
	virtual const FDateTime& GetDate() const override;
	virtual int32 GetCheckInIdentifier() const override;
	virtual int32 GetFileSize() const override;

	/** 작업 파일의 절대경로. */
	FString Filename;

	/** Content 기준 상대 경로. */
	FString Rel;

	/** 저장소 안의 내용 해시 (삭제된 리비전이면 빈 문자열). */
	FString Sha;

	/** 1부터 오름차순인 리비전 번호. */
	int32 RevisionNumber = 0;

	/** 이 리비전을 만든 확정 스냅샷 id. */
	int32 SnapshotId = 0;

	/** 확정 메시지. */
	FString Description;

	/** 확정 시각. */
	FDateTime Date = FDateTime();

	/** add | edit | delete */
	FString Action;

	int32 FileSize = 0;

	/** '#<스냅샷 id>' 형태의 표시용 리비전 문자열 (GetRevision 이 참조로 돌려주므로 미리 채워 둔다). */
	FString RevisionLabel;

	/** 항상 'local'. */
	FString UserName = TEXT("local");

	/** 언제나 빈 문자열. */
	FString ClientSpec;
};
